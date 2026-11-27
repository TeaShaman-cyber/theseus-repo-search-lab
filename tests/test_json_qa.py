import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'scripts' / 'check_json_contract.py'


class JsonQaTests(unittest.TestCase):
    def check_tracked(self, text: bytes):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            fixture = repo / 'tracked with spaces.json'
            fixture.write_bytes(text)
            subprocess.run(['git', '-C', str(repo), 'add', fixture.name], check=True)
            (repo / 'untracked.json').write_text('{invalid', encoding='utf-8')
            return subprocess.run(
                [sys.executable, str(CHECK)], cwd=repo,
                env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                capture_output=True, text=True, check=False,
            )

    def test_rejects_decoded_duplicate_names_in_tracked_files(self):
        for payload in (
            br'{"a":1,"\u0061":2}',
            br'{"nested":{"/":1,"\/":2}}',
            br'{"\u00e9":1,"\u00E9":2}',
            br'{"\ud83d\ude00":1,"\uD83D\uDE00":2}',
        ):
            with self.subTest(payload=payload):
                result = self.check_tracked(payload)
                self.assertEqual(result.returncode, 1)
                self.assertIn('duplicate JSON object key', result.stderr)
                self.assertIn('tracked with spaces.json', result.stderr)

    def test_accepts_distinct_decoded_names_and_separate_objects(self):
        result = self.check_tracked(br'{"\u00e9":1,"e\u0301":2,"x":{"a":1},"y":{"a":2}}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('STRICT_JSON_TRACKED_PASS files=1', result.stdout)

    def test_rejects_invalid_utf8_and_nonstandard_json(self):
        for payload in (b'{"a":"\xff"}', b'{"a":NaN}', b'{}{}'):
            with self.subTest(payload=payload):
                result = self.check_tracked(payload)
                self.assertEqual(result.returncode, 1)
                self.assertIn('tracked with spaces.json', result.stderr)
