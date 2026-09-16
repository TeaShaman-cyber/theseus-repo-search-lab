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
    flags: list[bool] = []
    block_depth = 0
    for line in lines:
        stripped = line.strip()
        is_comment = block_depth > 0 or stripped.startswith("--") or stripped.startswith("/-")
        flags.append(is_comment)
        block_depth += line.count("/-")
        block_depth -= line.count("-/")
        if block_depth < 0:
            block_depth = 0
    return flags


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




def _tracked_lean_files(source_root: Path) -> list[Path]:
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
            path.relative_to(source_root.resolve())
        except ValueError:
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(source_root).as_posix())

def scan_lean_sources(
    source_root: Path, *, source_commit: str, tracked_only: bool = False
) -> list[SourceChunk]:
    source_root = source_root.resolve()
    chunks: list[SourceChunk] = []
    if tracked_only:
        files = _tracked_lean_files(source_root)
    else:
        files = sorted(
            (
                path
                for path in source_root.rglob("*.lean")
                if ".lake" not in path.relative_to(source_root).parts
            ),
            key=lambda path: path.relative_to(source_root).as_posix(),
        )

    for path in files:
        relative_path = path.relative_to(source_root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        comment_flags = _comment_lines(lines)
        declarations: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            if comment_flags[index]:
                continue
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
