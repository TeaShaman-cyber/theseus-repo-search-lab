from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_ten_proofs_build_profile.sh"


class TenProofsBuildProfileTests(unittest.TestCase):
    def test_profile_runner_has_requested_five_profiles_and_four_factors(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for profile in (
            "all-in",
            "sphere-prestage",
            "long-roots-prestage",
            "lean-j2",
            "lean-memory-12288",
        ):
            self.assertIn(profile, text)
        self.assertIn('-KweakLeanArgs=-j2', text)
        self.assertIn('-KmoreLeanArgs=-M12288', text)
        self.assertIn('prestage-SpherePacking', text)
        for root in ("GapCVP", "ConnesRigidity", "QuantumParallelRepetition", "MetricCodes"):
            self.assertIn(root, text)

    def test_profile_runner_keeps_telemetry_out_of_heavy_filesystem_scans(self):
        text = SCRIPT.read_text(encoding="utf-8")
        heartbeat = text.split("heartbeat() {", 1)[1].split("heartbeat & heartbeat_pid=$!", 1)[0]
        self.assertIn("ci_telemetry.py snapshot", heartbeat)
        for forbidden in ("du -", "find ", "git ", "gh ", "curl ", "lake "):
            self.assertNotIn(forbidden, heartbeat)


if __name__ == "__main__":
    unittest.main()
