"""Compare lexical chunks with the native Lean declaration-range witness."""
import re
import sys
from pathlib import Path

from theseus_repo_search.sources import _chunks_from_text


def main():
    source, log = map(Path, sys.argv[1:])
    observed = re.findall(r"LEX_ORACLE ([\w.]+) (\d+) (\d+)", log.read_text())
    if len(observed) != 6 or len({row[0] for row in observed}) != 6:
        raise ValueError("missing or duplicate native Lean ranges")
    chunks = _chunks_from_text(source.name, source.read_text(), source_revision="oracle")
    if [c.declaration_hint for c in chunks] != [row[0] for row in observed]:
        raise ValueError("native declaration names differ from lexical hints")
    for chunk, (name, start, end) in zip(chunks, observed, strict=True):
        # Chunk boundaries may include documentation and intervening commands.
        if not chunk.source_start_line <= int(start) <= int(end) <= chunk.source_end_line:
            raise ValueError(f"native declaration span outside lexical chunk: {name}")
    print("LEAN_NATIVE_LEXICAL_ORACLE_PASS", len(chunks))


if __name__ == "__main__":
    main()
