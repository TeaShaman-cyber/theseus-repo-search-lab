from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_telemetry.py"


class CiTelemetryTests(unittest.TestCase):
    def test_snapshot_reads_bounded_procfs_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            (proc / "pressure").mkdir(parents=True)
            (proc / "meminfo").write_text(
                "MemTotal:       16000000 kB\nMemAvailable:    4242424 kB\n",
                encoding="utf-8",
            )
            (proc / "vmstat").write_text(
                "pgmajfault 1234\nworkingset_refault_file 5678\n",
                encoding="utf-8",
            )
            (proc / "pressure" / "memory").write_text(
                "some avg10=1.25 avg60=0.50 avg300=0.10 total=1\n"
                "full avg10=0.75 avg60=0.25 avg300=0.05 total=1\n",
                encoding="utf-8",
            )
            lean = proc / "101"
            lean.mkdir()
            (lean / "comm").write_text("lean\n", encoding="utf-8")
            (lean / "status").write_text("Name:\tlean\nVmRSS:\t2048 kB\n", encoding="utf-8")
            other = proc / "202"
            other.mkdir()
            (other / "comm").write_text("bash\n", encoding="utf-8")
            (other / "status").write_text("VmRSS:\t9999 kB\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "python3", str(SCRIPT), "snapshot",
                    "--phase", "build",
                    "--elapsed-seconds", "60",
                    "--proc-root", str(proc),
                    "--disk-path", str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            line = result.stdout.strip()
            self.assertTrue(line.startswith("telemetry "))
            self.assertIn("phase=build", line)
            self.assertIn("elapsed_seconds=60", line)
            self.assertIn("mem_available_kib=4242424", line)
            self.assertIn("pgmajfault=1234", line)
            self.assertIn("workingset_refault_file=5678", line)
            self.assertIn("memory_psi_some_avg10=1.25", line)
            self.assertIn("memory_psi_full_avg10=0.75", line)
            self.assertIn("lean_workers=1", line)
            self.assertIn("lean_rss_kib=2048", line)

    def test_snapshot_degrades_missing_optional_procfs_to_zero_or_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            proc.mkdir()
            result = subprocess.run(
                [
                    "python3", str(SCRIPT), "snapshot",
                    "--phase", "extract",
                    "--elapsed-seconds", "0",
                    "--proc-root", str(proc),
                    "--disk-path", str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            line = result.stdout.strip()
            self.assertIn("mem_available_kib=0", line)
            self.assertIn("pgmajfault=0", line)
            self.assertIn("workingset_refault_file=0", line)
            self.assertIn("memory_psi_some_avg10=NA", line)
            self.assertIn("memory_psi_full_avg10=NA", line)
            self.assertIn("lean_workers=0", line)
            self.assertIn("lean_rss_kib=0", line)


if __name__ == "__main__":
    unittest.main()
