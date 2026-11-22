import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/riemann-corpus-shard-pilot.yml")


class RiemannPilotWorkflowTests(unittest.TestCase):
    def test_adapted_extractor_env_write_stays_on_one_yaml_line(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "printf 'ADAPTED_EXTRACTOR_SHA256=%s\\n' "
            '"$adapted_sha" >> "$GITHUB_ENV"',
            text,
        )
        self.assertNotIn("\n' \"$adapted_sha\" >> \"$GITHUB_ENV\"", text)


if __name__ == "__main__":
    unittest.main()
