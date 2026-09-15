"""Exercise the CLI against a controlled ADB boundary; no device is touched."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "monitor.py"
spec = importlib.util.spec_from_file_location("monitor", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def fake_adb():
    args = sys.argv[sys.argv.index("--fake-adb") + 1:][2:]
    if args == ["get-state"]:
        print("device")
    elif args[:3] == ["shell", "pm", "path"]:
        print("package:/data/app/base.apk")
    elif args[:2] == ["shell", "pidof"]:
        print(Path(os.environ["MONITOR_TEST_PID"]).read_text())
    elif args[:3] == ["shell", "dumpsys", "meminfo"]:
        print("Native Heap 100 0 0\nDalvik Heap 50 0 0\nGraphics: 20\nTOTAL PSS: 200 TOTAL RSS: 300 TOTAL SWAP PSS: 0")
    elif args[:3] == ["shell", "dumpsys", "cpuinfo"]:
        print("  2.5% 42/com.example.app: 1% user + 1.5% kernel")
    elif args[:3] == ["shell", "dumpsys", "gfxinfo"]:
        print("Total frames rendered: 100\nJanky frames: 4 (4.0%)")
    elif args[:2] == ["shell", "cat"]:
        print("Threads:\t12")
    elif args and args[0] == "logcat":
        print("app log " + args[1], flush=True)
        time.sleep(20)
    else:
        print("test metadata")


class MonitorTests(unittest.TestCase):
    def test_metrics_versions_and_missing_values(self):
        result = monitor.metrics(" TOTAL 456 0 0 0 999 0\n", " 9% 1/com.example.app:remote: 1% user", "", "com.example.app")
        self.assertEqual(result["pss_kib"], 456)
        self.assertIsNone(result["rss_kib"])
        self.assertIsNone(result["cpu_pct"])
        self.assertIsNone(result["frames"])
        self.assertIsNone(monitor.metrics("permission denied", "", "", "com.example.app")["pss_kib"])

    def test_background_lifecycle_and_report(self):
        with tempfile.TemporaryDirectory(prefix="monitor test ") as temporary:
            root = Path(temporary)
            pid_file = root / "pid"
            pid_file.write_text("42")
            fake = root / "adb"
            fake.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(__file__) + ' --fake-adb "$@"\n')
            fake.chmod(0o700)
            environment = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], MONITOR_TEST_PID=str(pid_file))
            directory = root / "run"

            def cli(*args):
                completed = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(completed.stdout)

            def until(predicate):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(0.05)
                self.fail("Collector did not reach expected state")

            def sampled(pid):
                return any(row["pid"] == pid for row in monitor.rows(directory / "samples.jsonl"))

            try:
                started = cli("start", "--device", "test-device", "--package", "com.example.app", "--output", directory, "--interval", "0.05", "--duration", "15")
                self.assertTrue(started["collector_active"])
                until(lambda: sampled("42"))
                cli("mark", directory, "Opened wallet")
                pid_file.write_text("43")
                until(lambda: sampled("43"))
                pid_file.write_text("")
                until(lambda: sampled(None))
                self.assertTrue(cli("stop", directory)["stop_requested"])
                until(lambda: not cli("status", directory)["collector_active"])
                summary = cli("report", directory)
                self.assertEqual(summary["state"]["status"], "stopped")
                self.assertEqual(summary["metrics"]["pss_kib"]["peak"], 200)
                self.assertEqual(summary["metrics"]["cpu_pct"]["last"], 2.5)
                self.assertEqual(summary["markers"][0]["label"], "Opened wallet")
                self.assertEqual([entry["pid"] for entry in summary["pid_transitions"]], ["42", "43", None])
                self.assertGreater(summary["incomplete_samples"], 0)
                self.assertIn("--pid=43", (directory / "logcat.txt").read_text())
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                with (directory / "samples.jsonl").open("a") as stream:
                    stream.write('{"partial":')
                self.assertEqual(cli("report", directory)["samples"], summary["samples"])
                # Expiry and stale-state reporting must not signal a stored PID.
                second = root / "expires"
                cli("start", "--device", "test-device", "--package", "com.example.app", "--output", second, "--duration", "0.1")
                until(lambda: not cli("status", second)["collector_active"])
                self.assertEqual(cli("status", second)["status"], "completed")
                monitor.save(second / "state.json", {"status": "running", "pid": os.getpid()})
                self.assertEqual(cli("stop", second)["status"], "interrupted")
            finally:
                for run in (directory, root / "expires"):
                    if (run / "state.json").exists():
                        (run / "stop").touch()
                        until(lambda: not monitor.status(run)["collector_active"])

    def test_untrusted_arguments_rejected_before_adb(self):
        for args in (("--package", "com.example;touch /tmp/no"), ("--interval", "nan"), ("--duration", "0")):
            result = subprocess.run([sys.executable, str(SCRIPT), "start", "--device", "test", "--package", "com.example.app", *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    if "--fake-adb" in sys.argv:
        fake_adb()
    else:
        unittest.main()
