"""Lexical Lean source chunking with provenance; this module is not a Lean parser."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path, PurePosixPath

from .errors import RepoSearchError
from .model import Node, SourceChunk


DECL_RE = re.compile(
    r"^\s*(?:protected\s+|private\s+|noncomputable\s+|unsafe\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|class|inductive|instance)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_'\.]*)"
)


def _comment_lines(lines: list[str]) -> list[bool]:
    """Return whether each line begins in comment context.

    This is intentionally only the lexical state needed by the chunker, not a
    full Lean parser. Block comments are nested; comment delimiters inside
    strings and line comments do not affect block depth.
    """
    flags: list[bool] = []
    block_depth = 0

    for line in lines:
        line_starts_in_block = block_depth > 0
        first_token_is_comment = False
        code_seen = False
        in_string = False
        escaped = False
        index = 0

        while index < len(line):
            if block_depth > 0:
                if line.startswith("/-", index):
                    block_depth += 1
                    index += 2
                    continue
                if line.startswith("-/", index):
                    block_depth -= 1
                    index += 2
                    continue
                index += 1
                continue

            char = line[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue

            if line.startswith("--", index):
                if not code_seen:
                    first_token_is_comment = True
                break
            if line.startswith("/-", index):
                if not code_seen:
                    first_token_is_comment = True
                block_depth += 1
                index += 2
                continue
            if char == '"':
                code_seen = True
                in_string = True
                index += 1
                continue
            if not char.isspace():
                code_seen = True
            index += 1

        flags.append(line_starts_in_block or first_token_is_comment)

    return flags


def _code_lines(lines: list[str]) -> list[str]:
    """Return source lines with actual comments blanked for declaration matching."""
    rendered: list[str] = []
    block_depth = 0

    for line in lines:
        out: list[str] = []
        in_string = False
        escaped = False
        index = 0

        while index < len(line):
            if block_depth > 0:
                if line.startswith("/-", index):
                    block_depth += 1
                    out.extend("  ")
                    index += 2
                    continue
                if line.startswith("-/", index):
                    block_depth -= 1
                    out.extend("  ")
                    index += 2
                    continue
                out.append("\n" if line[index] == "\n" else " ")
                index += 1
                continue

            char = line[index]
            if in_string:
                out.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue

            if line.startswith("--", index):
                out.extend("\n" if value == "\n" else " " for value in line[index:])
                index = len(line)
                continue
            if line.startswith("/-", index):
                block_depth += 1
                out.extend("  ")
                index += 2
                continue
            out.append(char)
            if char == '"':
                in_string = True
            index += 1

        rendered.append("".join(out))

    return rendered


def _chunk_starts(lines: list[str], declaration_lines: list[int]) -> list[int]:
    comment_flags = _comment_lines(lines)
    starts: list[int] = []
    for declaration_index in declaration_lines:
        start = declaration_index
        cursor = declaration_index - 1
        while cursor >= 0 and (not lines[cursor].strip() or comment_flags[cursor]):
            start = cursor
            cursor -= 1
        starts.append(start)
    return starts




def tracked_lean_files(
    source_root: Path, *, exclude_prefixes: tuple[str, ...] = ()
) -> list[Path]:
    try:
        repo_root_text = subprocess.check_output(
            ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
        repo_root = Path(repo_root_text).resolve()
        relative_root = source_root.resolve().relative_to(repo_root)
        pathspec = "." if relative_root == Path(".") else relative_root.as_posix()
        output = subprocess.check_output(
            ["git", "-C", str(repo_root), "ls-files", "-z", "--", pathspec],
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise RuntimeError(f"cannot enumerate tracked source files: {source_root}") from exc

    files: list[Path] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        path = (repo_root / relative).resolve()
        if path.suffix != ".lean":
            continue
        try:
            relative_to_source = path.relative_to(source_root.resolve())
        except ValueError:
            continue
        relative_posix = relative_to_source.as_posix()
        if any(relative_posix.startswith(prefix) for prefix in exclude_prefixes):
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(source_root).as_posix())

def _chunks_from_text(
    relative_path: str,
    text: str,
    *,
    source_revision: str,
) -> list[SourceChunk]:
    lines = text.splitlines(keepends=True)
    code_lines = _code_lines(lines)
    declarations: list[tuple[int, str]] = []
    for index, line in enumerate(code_lines):
        match = DECL_RE.match(line)
        if match:
            declarations.append((index, match.group("name")))
    if not declarations:
        return []

    declaration_lines = [index for index, _ in declarations]
    starts = _chunk_starts(lines, declaration_lines)
    chunks: list[SourceChunk] = []
    for position, ((_, declaration_name), start) in enumerate(zip(declarations, starts)):
        end = starts[position + 1] - 1 if position + 1 < len(starts) else len(lines) - 1
        chunk_text = "".join(lines[start : end + 1])
        start_line = start + 1
        end_line = end + 1
        chunks.append(
            SourceChunk(
                id=f"src:{relative_path}:{start_line}:{end_line}",
                source_commit=source_revision,
                source_path=relative_path,
                source_start_line=start_line,
                source_end_line=end_line,
                declaration_hint=declaration_name,
                text=chunk_text,
                content_sha256=sha256(chunk_text.encode()).hexdigest(),
            )
        )
    return chunks


def _source_mismatch(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_SOURCE_MISMATCH", message)


def _read_stable_regular_bytes(target: Path, source_path: str) -> bytes:
    try:
        before = target.lstat()
    except OSError as exc:
        raise _source_mismatch(f"authoritative archive source missing: {source_path}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise _source_mismatch(f"authoritative archive source type changed: {source_path}")
    try:
        with target.open("rb") as fh:
            opened = os.fstat(fh.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise _source_mismatch(
                    f"authoritative archive source changed while opening: {source_path}"
                )
            return fh.read()
    except RepoSearchError:
        raise
    except OSError as exc:
        raise _source_mismatch(f"cannot read authoritative archive source: {source_path}") from exc


def scan_manifest_backed_lean_sources(
    source_root: Path,
    *,
    source_revision: str,
    member_manifest_path: Path,
    expected_manifest_sha256: str | None = None,
    exclude_prefixes: tuple[str, ...] = (),
) -> list[SourceChunk]:
    source_root = source_root.resolve()
    try:
        manifest_bytes = member_manifest_path.read_bytes()
        if (
            expected_manifest_sha256 is not None
            and sha256(manifest_bytes).hexdigest() != expected_manifest_sha256
        ):
            raise _source_mismatch(
                "archive member manifest changed before source consumption"
            )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except RepoSearchError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"invalid archive member manifest: {exc}",
        ) from exc
    if not isinstance(manifest, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive member manifest root must be an object",
        )
    if manifest.get("schema") != "theseus.archive-member-manifest.v1":
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "unsupported archive member manifest schema",
        )
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("kind") != "archive":
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive member manifest source must be explicit archive authority",
        )
    if source.get("sha256") != source_revision:
        raise _source_mismatch("archive member manifest revision does not match source revision")
    members = manifest.get("members")
    if not isinstance(members, list):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive member manifest members must be an array",
        )

    chunks: list[SourceChunk] = []
    seen_source_paths: set[str] = set()
    for item in members:
        if not isinstance(item, dict) or set(item) != {"path", "source_path", "sha256"}:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                "archive member manifest entry has invalid fields",
            )
        source_path = item.get("source_path")
        expected_sha256 = item.get("sha256")
        if source_path is None:
            continue
        if not isinstance(source_path, str) or not source_path:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                "archive member manifest source_path must be a string or null",
            )
        relative = PurePosixPath(source_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != source_path
        ):
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"unsafe archive member source_path: {source_path}",
            )
        if source_path in seen_source_paths:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"duplicate archive member source_path: {source_path}",
            )
        seen_source_paths.add(source_path)
        if not source_path.endswith(".lean"):
            continue
        if any(source_path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_sha256)
        ):
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"invalid archive member hash for {source_path}",
            )
        target = source_root.joinpath(*relative.parts)
        data = _read_stable_regular_bytes(target, source_path)
        if sha256(data).hexdigest() != expected_sha256:
            raise _source_mismatch(f"authoritative archive source hash mismatch: {source_path}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"authoritative Lean source is not UTF-8: {source_path}",
            ) from exc
        chunks.extend(
            _chunks_from_text(
                source_path,
                text,
                source_revision=source_revision,
            )
        )

    return sorted(
        chunks,
        key=lambda chunk: (chunk.source_path, chunk.source_start_line, chunk.id),
    )


def scan_lean_sources(
    source_root: Path, *, source_commit: str, tracked_only: bool = False,
    exclude_prefixes: tuple[str, ...] = (),
) -> list[SourceChunk]:
    source_root = source_root.resolve()
    chunks: list[SourceChunk] = []
    if tracked_only:
        files = tracked_lean_files(source_root, exclude_prefixes=exclude_prefixes)
    else:
        files = sorted(
            (
                path
                for path in source_root.rglob("*.lean")
                if ".lake" not in path.relative_to(source_root).parts
                and not any(path.relative_to(source_root).as_posix().startswith(prefix) for prefix in exclude_prefixes)
            ),
            key=lambda path: path.relative_to(source_root).as_posix(),
        )

    for path in files:
        relative_path = path.relative_to(source_root).as_posix()
        text = path.read_text(encoding="utf-8")
        chunks.extend(
            _chunks_from_text(
                relative_path,
                text,
                source_revision=source_commit,
            )
        )

    return sorted(chunks, key=lambda chunk: (chunk.source_path, chunk.source_start_line, chunk.id))


def bind_manifest_backed_node_sources(
    nodes: list[Node], sources: list[SourceChunk]
) -> list[Node]:
    def matches_node(chunk: SourceChunk, node: Node, expected_path: str) -> bool:
        hint = chunk.declaration_hint
        if chunk.source_path != expected_path or hint is None:
            return False
        if hint == node.name or hint == node.full_name:
            return True
        return node.full_name.endswith(f".{hint}")

    bound: list[Node] = []
    for node in nodes:
        expected_path = f"{node.module.replace('.', '/')}.lean"
        matches = [
            chunk
            for chunk in sources
            if matches_node(chunk, node, expected_path)
        ]
        if len(matches) != 1:
            raise _source_mismatch(
                "archive node is not backed by exactly one authoritative source chunk: "
                f"{node.full_name} -> {expected_path}"
            )
        chunk = matches[0]
        if chunk.source_revision != node.source_revision:
            raise _source_mismatch(
                f"archive node/source revision mismatch: {node.full_name}"
            )
        bound.append(
            replace(
                node,
                source_path=chunk.source_path,
                source_start_line=chunk.source_start_line,
                source_end_line=chunk.source_end_line,
            )
        )
    return bound


def bind_node_sources(nodes: list[Node], sources: list[SourceChunk]) -> list[Node]:
    by_path_and_name: dict[tuple[str, str], list[SourceChunk]] = {}
    for chunk in sources:
        if chunk.declaration_hint is None:
            continue
        by_path_and_name.setdefault(
            (chunk.source_path, chunk.declaration_hint), []
        ).append(chunk)

    bound: list[Node] = []
    for node in nodes:
        if node.source_path is not None:
            bound.append(node)
            continue
        expected_path = f"{node.module.replace('.', '/')}.lean"
        matches = by_path_and_name.get((expected_path, node.name), [])
        if len(matches) != 1:
            bound.append(node)
            continue
        chunk = matches[0]
        bound.append(
            replace(
                node,
                source_path=chunk.source_path,
                source_start_line=chunk.source_start_line,
                source_end_line=chunk.source_end_line,
            )
        )
    return bound
