"""Lexical Lean source chunking with provenance; this module is not a Lean parser."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

from .model import SourceChunk


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


def scan_lean_sources(source_root: Path, *, source_commit: str) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    files = sorted(
        source_root.rglob("*.lean"),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )

    for path in files:
        relative_path = path.relative_to(source_root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        declarations: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
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
