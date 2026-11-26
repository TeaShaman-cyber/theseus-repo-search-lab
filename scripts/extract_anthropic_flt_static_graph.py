from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.anthropic_flt_static import parse_static_export


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    root = args.source_root.resolve()
    meta = (root / "html/data/meta.js").read_text(encoding="utf-8")
    edges = (root / "html/data/edges.js").read_text(encoding="utf-8")
    graph = parse_static_export(meta, edges)

    theorem_root = root / "Theorems/Thm_fermat_last_theorem.lean"
    if not theorem_root.is_file():
        raise SystemExit("BLOCKED_ARTIFACT_INTEGRITY: missing FLT root theorem source")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(graph, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
