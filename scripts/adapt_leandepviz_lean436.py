#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

PINNED_UPSTREAM_SHA256 = "7051af16c579dc425859442bbe7b647eaea78a581ddf685c5951c2b7bf28ceed"

_SINGLE_REWRITE = (
    "let part := part.trim",
    "let part := part.trimAscii.copy",
)
_MAP_REWRITE = (
    "|>.map String.trim |>.filter",
    "|>.map (fun s => s.trimAscii.copy) |>.filter",
)


def adapt_source(source: bytes, *, expected_sha256: str = PINNED_UPSTREAM_SHA256) -> bytes:
    observed = hashlib.sha256(source).hexdigest()
    if observed != expected_sha256:
        raise ValueError(
            f"LeanDepViz input SHA-256 mismatch: expected {expected_sha256}, observed {observed}"
        )
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("LeanDepViz input must be UTF-8") from exc

    old, new = _SINGLE_REWRITE
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly 1 occurrence of {old!r}, observed {count}")
    text = text.replace(old, new)

    old, new = _MAP_REWRITE
    count = text.count(old)
    if count != 2:
        raise ValueError(f"expected exactly 2 occurrences of {old!r}, observed {count}")
    text = text.replace(old, new)

    if "String.trim" in text or "part.trim\n" in text:
        raise ValueError("legacy trim API remains after Lean 4.36 adaptation")
    return text.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Adapt the exact pinned LeanDepViz Main.lean to Lean 4.36 String trimming APIs."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-input-sha256", default=PINNED_UPSTREAM_SHA256)
    args = parser.parse_args()

    adapted = adapt_source(
        args.input.read_bytes(),
        expected_sha256=args.expected_input_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(adapted)
    print(f"adapted_sha256={hashlib.sha256(adapted).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
