"""Build a source-free Repo Search preview from a verified upstream deps pilot.

The pilot ZIP remains the raw occurrence authority for this diagnostic only.
This does not publish or accept a corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from theseus_repo_search.artifact import artifact_identity, write_artifact
from theseus_repo_search.model import ArtifactScope, ProducerPin, SourceChunk
from theseus_repo_search.olean_projection import normalize_olean_references
from theseus_repo_search.projection import build_projection
from theseus_repo_search.sources import (
    _chunks_from_text,
    bind_node_sources,
    lean_module_source_path,
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sources_from_git(
    repo: Path, commit: str, modules: tuple[str, ...], subdir: str
) -> list[SourceChunk]:
    paths = {
        str(
            PurePosixPath(subdir) / lean_module_source_path(m)
        ): lean_module_source_path(m)
        for m in modules
    }
    if len(paths) != len(modules) or any(
        PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts for p in paths
    ):
        raise ValueError("invalid or colliding module paths")
    chunks: list[SourceChunk] = []
    seen: set[str] = set()
    with subprocess.Popen(
        [
            "git",
            "-C",
            str(repo),
            "archive",
            "--format=tar",
            commit,
            "--",
            *sorted(paths),
        ],
        stdout=subprocess.PIPE,
    ) as proc:
        assert proc.stdout is not None
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    if (
                        not member.isfile()
                        or member.name not in paths
                        or member.name in seen
                    ):
                        raise ValueError("unexpected source archive member")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("source archive member unreadable")
                    chunks.extend(
                        _chunks_from_text(
                            paths[member.name],
                            stream.read().decode("utf-8"),
                            source_revision=commit,
                        )
                    )
                    seen.add(member.name)
        except BaseException:
            proc.kill()
            raise
        if proc.wait() != 0 or seen != set(paths):
            raise ValueError("pinned source coverage mismatch")
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--pilot-sha256", required=True)
    parser.add_argument("--source-git", type=Path, required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-subdir", default="src")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise ValueError("source commit must be an exact Git SHA-1")
    if digest(args.pilot) != args.pilot_sha256:
        raise ValueError("pilot ZIP digest mismatch")
    with zipfile.ZipFile(args.pilot) as z:
        receipt = json.loads(z.read("receipt.json"))
        if (
            receipt["source_commit"] != args.source_commit
            or receipt["schema"] != "theseus.olean-deps-pilot.v1"
        ):
            raise ValueError("pilot source or schema mismatch")
        if len(z.namelist()) != len(set(z.namelist())) or set(z.namelist()) != set(
            receipt["file_hashes"]
        ) | {"receipt.json"}:
            raise ValueError("pilot member set mismatch")
        for name, h in receipt["file_hashes"].items():
            with z.open(name) as stream:
                member_hash = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    member_hash.update(block)
                if member_hash.hexdigest() != h:
                    raise ValueError(f"pilot member digest mismatch: {name}")
        modules = tuple(sorted(z.read("project-closure.txt").decode().splitlines()))
        if not modules or len(modules) != len(set(modules)):
            raise ValueError("invalid pilot module closure")
        tool_commit = receipt["upstream_commit"]
        tool_hash = z.read("upstream-binary.sha256").decode().split()[0]
        if not re.fullmatch(r"[0-9a-f]{40}", tool_commit) or not re.fullmatch(
            r"[0-9a-f]{64}", tool_hash
        ):
            raise ValueError("invalid upstream producer identity")
        with z.open("rh-deps.jsonl") as stream:
            nodes, edges, report = normalize_olean_references(
                map(json.loads, stream),
                source_commit=args.source_commit,
                modules=modules,
                producer_ref=f"sdiehl/olean-export@{tool_commit}",
            )
    sources = sources_from_git(
        args.source_git, args.source_commit, modules, args.source_subdir
    )
    nodes = bind_node_sources(nodes, sources)
    manifest = write_artifact(
        args.out / "artifact",
        nodes=nodes,
        edges=edges,
        sources=sources,
        source_repo=args.source_repo,
        source_commit=args.source_commit,
        source_subdir=args.source_subdir,
        producer=ProducerPin(
            "olean-export-deps-preview", "sdiehl/olean-export", tool_commit, tool_hash
        ),
        scope=ArtifactScope(modules, "internal_only"),
        created_from_authoritative_commit=False,
    )
    fingerprint = build_projection(args.out / "artifact", args.out / "index.sqlite")
    report.update(
        {
            "schema": "theseus.olean-search-preview.v1",
            "source_repo": args.source_repo,
            "source_commit": args.source_commit,
            "pilot_sha256": args.pilot_sha256,
            "pilot_producer_commit": receipt["producer_commit"],
            "upstream_commit": tool_commit,
            "upstream_binary_sha256": tool_hash,
            "source_chunks": len(sources),
            "source_bound_nodes": sum(n.source_path is not None for n in nodes),
            "artifact_identity": artifact_identity(manifest),
            "projection_fingerprint": fingerprint,
            "created_from_authoritative_commit": False,
            "accepted": False,
        }
    )
    (args.out / "preview-receipt.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "ambiguous_names"}))


if __name__ == "__main__":
    main()
