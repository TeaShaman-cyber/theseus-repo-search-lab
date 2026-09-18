"""Lexical Lean source chunking with provenance; this module is not a Lean parser."""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

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
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        code_lines = _code_lines(lines)
        declarations: list[tuple[int, str]] = []
        for index, line in enumerate(code_lines):
            match = DECL_RE.match(line)
            if match:
                declarations.append((index, match.group("name")))
        if not declarations:
            continue

        declaration_lines = [index for index, _ in declarations]
        starts = _chunk_starts(lines, declaration_lines)
        for position, ((_, declaration_name), start) in enumerate(zip(declarations, starts)):
            end = starts[position + 1] - 1 if position + 1 < len(starts) else len(lines) - 1
            text = "".join(lines[start : end + 1])
            start_line = start + 1
            end_line = end + 1
            chunks.append(
                SourceChunk(
                    id=f"src:{relative_path}:{start_line}:{end_line}",
                    source_commit=source_commit,
                    source_path=relative_path,
                    source_start_line=start_line,
                    source_end_line=end_line,
                    declaration_hint=declaration_name,
                    text=text,
                    content_sha256=sha256(text.encode()).hexdigest(),
                )
            )

    return sorted(chunks, key=lambda chunk: (chunk.source_path, chunk.source_start_line, chunk.id))


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
