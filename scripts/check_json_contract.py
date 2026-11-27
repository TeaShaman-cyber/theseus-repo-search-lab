"""QA-only checks using the same strict parser as Repository Search."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from theseus_repo_search.json_contract import strict_json_loads

REVISION = '1ef36fa01286573e846ac449e8683f8833c5b26a'
DUPLICATE_POLICY = {'y_object_duplicated_key.json', 'y_object_duplicated_key_and_value.json'}
DEEP_INVALID = {'n_structure_100000_opening_arrays.json', 'n_structure_open_array_object.json'}


def check_tracked() -> int:
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).rstrip('\n'))
    paths = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--', '*.json']).split(b'\0')
    files = [root / os.fsdecode(path) for path in paths if path]
    if not files:
        print('No tracked JSON files', file=sys.stderr)
        return 1
    failures = 0
    for path in files:
        try:
            strict_json_loads(path.read_bytes().decode('utf-8'))
        except (OSError, ValueError, RecursionError) as error:
            print(f'{path.relative_to(root)}: {error}', file=sys.stderr)
            failures += 1
    if failures:
        return 1
    print(f'STRICT_JSON_TRACKED_PASS files={len(files)}')
    return 0


def check_corpus(root: Path) -> int:
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'])
    if revision != REVISION or status:
        raise ValueError('JSONTestSuite checkout must be clean at the pinned revision')
    files = sorted(path for path in (root / 'test_parsing').glob('*.json') if path.name.startswith(('y_', 'n_', 'i_')))
    counts: Counter[str] = Counter()
    failures = []
    for path in files:
        kind = path.name[0]
        counts[kind] += 1
        raw = ''
        result = None
        accepted = False
        try:
            raw = path.read_bytes().decode('utf-8')
            result = strict_json_loads(raw)
            accepted = True
        except ValueError:
            pass
        except RecursionError:
            if path.name not in DEEP_INVALID and kind != 'i':
                raise
            counts['resource_rejected'] += 1
        if kind == 'i':
            counts['implementation_defined_accepted'] += int(accepted)
        elif kind == 'n' or path.name in DUPLICATE_POLICY:
            if accepted:
                failures.append(f'{path.name}: unexpectedly accepted')
            else:
                counts['invalid_rejected' if kind == 'n' else 'policy_rejected'] += 1
        elif not accepted or result != json.loads(raw):
            failures.append(f'{path.name}: rejected or decoded value mismatch')
        else:
            counts['accepted'] += 1
    expected = {'y': 95, 'n': 188, 'i': 35, 'accepted': 93, 'policy_rejected': 2, 'invalid_rejected': 188}
    for key, value in expected.items():
        if counts[key] != value:
            failures.append(f'{key}: expected {value}, observed {counts[key]}')
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        return 1
    print('JSON_CONFORMANCE_PASS', json.dumps(dict(counts), sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, help='clean pinned JSONTestSuite checkout')
    args = parser.parse_args()
    return check_tracked() if args.corpus is None else check_corpus(args.corpus)


if __name__ == '__main__':
    raise SystemExit(main())
