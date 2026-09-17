from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from theseus_repo_search.graph import dependencies, reverse_dependencies
from theseus_repo_search.retrieval import search
from theseus_repo_search.sources import tracked_lean_files


TIGHT_QUERY = "tight pairs extremal"
CHEBYSHEV_QUERY = "Chebyshev Mertens"
BASELINE_RE = re.compile(r"certificate|trace|frobenius|moment", re.IGNORECASE)


def _top10_contains(db_path: Path, query: str, declaration_hint: str) -> tuple[bool, list[dict[str, object]]]:
    hits = search(db_path, query, limit=10)
    payload = [
        {
            "rank": index + 1,
            "declaration_id": hit.declaration_id,
            "declaration_hint": hit.declaration_hint,
            "source_path": hit.source_path,
            "source_start_line": hit.source_start_line,
            "source_end_line": hit.source_end_line,
            "score": hit.score,
        }
        for index, hit in enumerate(hits)
    ]
    return any(hit.declaration_hint == declaration_hint for hit in hits), payload


def _baseline_unique_paths(source_root: Path) -> int:
    root = source_root.resolve()
    matched: set[str] = set()
    for path in tracked_lean_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if BASELINE_RE.search(text):
            matched.add(path.relative_to(root).as_posix())
    return len(matched)


def _fts_unique_paths(db_path: Path) -> tuple[int, list[str]]:
    hits = search(db_path, "certificate trace Frobenius moment", limit=10)
    paths = sorted({hit.source_path for hit in hits if hit.source_path is not None})
    return len(paths), paths


def run_replay(db_path: Path, source_root: Path) -> dict[str, object]:
    tight_deps = dependencies(db_path, "lemmaR_tight_two", depth=1)
    tight_targets = {str(edge["target_id"]) for edge in tight_deps.edges}
    required_tight = "lean:Zeta23.ZeroSide.TightMult.lemmaR_tight"
    if required_tight not in tight_targets:
        raise AssertionError(f"missing direct dependency: {required_tight}")

    rank_users = reverse_dependencies(db_path, "rank_trace_mult_k", depth=1)
    rank_sources = {str(edge["source_id"]) for edge in rank_users.edges}
    required_rank_user = "lean:Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k_le"
    if required_rank_user not in rank_sources:
        raise AssertionError(f"missing reverse dependency: {required_rank_user}")

    certificate = dependencies(db_path, "count_certificate", depth=5)
    certificate_targets = {str(edge["target_id"]) for edge in certificate.edges}
    required_moment = "lean:Zeta23.Assembly.N0star_lower_moment"
    if required_moment not in certificate_targets:
        raise AssertionError(f"missing certificate ancestor: {required_moment}")

    tight_found, tight_hits = _top10_contains(db_path, TIGHT_QUERY, "lemmaR_tight_two")
    if not tight_found:
        raise AssertionError("lemmaR_tight_two missing from top-10 grounded lexical replay")

    chebyshev_found, chebyshev_hits = _top10_contains(
        db_path, CHEBYSHEV_QUERY, "ChebyshevMertens"
    )
    if not chebyshev_found:
        raise AssertionError("ChebyshevMertens missing from top-10 lexical replay")

    baseline_paths = _baseline_unique_paths(source_root)
    fts_paths_count, fts_paths = _fts_unique_paths(db_path)
    if fts_paths_count > 10:
        raise AssertionError(f"FTS top-10 returned too many unique paths: {fts_paths_count}")
    if baseline_paths <= fts_paths_count:
        raise AssertionError(
            f"FTS did not reduce path scanning: baseline={baseline_paths}, fts={fts_paths_count}"
        )

    return {
        "status": "PASS",
        "graph": {
            "lemmaR_tight_two": {
                "required_target": required_tight,
                "edge_count": len(tight_deps.edges),
            },
            "rank_trace_mult_k": {
                "required_consumer": required_rank_user,
                "edge_count": len(rank_users.edges),
            },
            "count_certificate": {
                "required_ancestor": required_moment,
                "edge_count": len(certificate.edges),
                "max_depth": 5,
            },
        },
        "lexical": {
            "tight_query": TIGHT_QUERY,
            "tight_target": "lemmaR_tight_two",
            "tight_hits": tight_hits,
            "chebyshev_query": CHEBYSHEV_QUERY,
            "chebyshev_target": "ChebyshevMertens",
            "chebyshev_hits": chebyshev_hits,
        },
        "metrics": {
            "baseline_query": "certificate|trace|frobenius|moment",
            "baseline_unique_paths": baseline_paths,
            "fts_query": "certificate trace Frobenius moment",
            "fts_unique_paths": fts_paths_count,
            "fts_paths": fts_paths,
        },
        "scope": {
            "root_modules": list(tight_deps.scope_root_modules),
            "dependency_boundary": tight_deps.dependency_boundary,
            "complete_within_scope": tight_deps.complete_within_scope,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_replay(args.db, args.source_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
