import json
import os
import sys
import tempfile
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.replay_zeta23 import run_replay
from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin, SourceChunk
from theseus_repo_search.projection import build_projection


DESCRIPTOR = Path("producer/sources/zeta23.json")
COMMIT = "fbdc36bbf17d20af3fd0447c6d1a8a02773c9844"


def node(full_name: str) -> Node:
    return Node.from_lean(
        full_name=full_name,
        name=full_name.rsplit(".", 1)[-1],
        kind="thm",
        module=full_name.rsplit(".", 1)[0],
        source_commit=COMMIT,
    )


def edge(source: str, target: str) -> Edge:
    return Edge(
        source_id=f"lean:{source}",
        target_id=f"lean:{target}",
        relation="value_dependency",
        evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
        producer="cameronfreer/LeanDepViz@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )


def chunk(hint: str, path: str, text: str, line: int) -> SourceChunk:
    return SourceChunk(
        id=f"src:{path}:{line}:{line}",
        source_commit=COMMIT,
        source_path=path,
        source_start_line=line,
        source_end_line=line,
        declaration_hint=hint,
        text=text,
        content_sha256=sha256(text.encode()).hexdigest(),
    )


class ReplayZeta23Tests(unittest.TestCase):
    def test_registered_replay_passes_on_matching_graph_and_retrieval_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source_root = root / "source"
            source_root.mkdir()
            (source_root / "one.lean").write_text("trace moment\n", encoding="utf-8")
            (source_root / "two.lean").write_text("certificate\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", source_root], check=True)
            subprocess.run(["git", "-C", source_root, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", source_root, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", source_root, "add", "one.lean", "two.lean"], check=True)
            subprocess.run(["git", "-C", source_root, "commit", "-qm", "fixture"], check=True)
            generated = source_root / ".lake" / "packages" / "Fake.lean"
            generated.parent.mkdir(parents=True)
            generated.write_text("certificate trace moment\n", encoding="utf-8")
            (source_root / "scratch.lean").write_text("certificate trace moment\n", encoding="utf-8")

            artifact = root / "artifact"
            db = root / "index.sqlite"
            names = [
                "Zeta23.ZeroSide.TightMult.lemmaR_tight_two",
                "Zeta23.ZeroSide.TightMult.lemmaR_tight",
                "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k",
                "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k_le",
                "Zeta23.Assembly.count_certificate",
                "Zeta23.Assembly.N0star_lower_moment",
                "Zeta23.Hypotheses.ChebyshevMertens",
            ]
            write_artifact(
                artifact,
                nodes=[node(name) for name in names],
                edges=[
                    edge(names[0], names[1]),
                    edge(names[3], names[2]),
                    edge(names[4], names[5]),
                ],
                sources=[
                    chunk("lemmaR_tight_two", "Tight.lean", "simples doubles tight pairs extremal\n", 1),
                    chunk("ChebyshevMertens", "Hypotheses.lean", "Chebyshev Mertens arithmetic interface\n", 1),
                    chunk("count_certificate", "Certificate.lean", "certificate trace moment\n", 1),
                ],
                source_repo="anthropics/formal-math",
                source_commit=COMMIT,
                source_subdir="zeta23",
                producer=ProducerPin(
                    kind="lean-dep-viz",
                    tool_repo="cameronfreer/LeanDepViz",
                    tool_commit="b" * 40,
                    tool_hash="c" * 64,
                ),
                scope=ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only"),
                created_from_authoritative_commit=True,
            )
            build_projection(artifact, db)

            result = run_replay(db, artifact, DESCRIPTOR, source_root)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["provenance"], {
                "repo": "anthropics/formal-math",
                "commit": COMMIT,
                "subdir": "zeta23",
            })
            self.assertEqual(result["lexical"]["tight_query"], "tight pairs extremal")
            self.assertEqual(result["metrics"]["baseline_unique_paths"], 2)
            self.assertGreater(
                result["metrics"]["baseline_unique_paths"],
                result["metrics"]["fts_unique_paths"],
            )

            out = root / "replay.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = "src:."
            proc = subprocess.run(
                [
                    sys.executable, "scripts/replay_zeta23.py",
                    "--db", str(db),
                    "--artifact", str(artifact),
                    "--descriptor", str(DESCRIPTOR),
                    "--source-root", str(source_root),
                    "--out", str(out),
                ],
                cwd=Path.cwd(), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["provenance"]["repo"], "anthropics/formal-math")
