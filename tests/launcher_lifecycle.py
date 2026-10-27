"""Dependency-free lifecycle checks, mandatory on Linux and Windows."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from bzs import __version__, process_control as pc


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bzs-lifecycle-")
        self.env = patch.dict(os.environ, {"BZS_HOME": self.temp.name})
        self.env.start()
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        self.env.stop()
        self.temp.cleanup()

    def command(self, before="", ready=None, after=""):
        ready = ready or {"event":"ready", "component":"bzs-worker", "host":"127.0.0.1", "port":12345, "version":__version__}
        return [sys.executable, "-c", "import os,time,json,signal;" + before + f"print({json.dumps(ready)!r},flush=True);" + after + "time.sleep(60)"]

    def test_invalid_pids_never_probe_os(self):
        for pid in (0,1,-1,2**31,2**80,True,None,"123"):
            with self.subTest(pid=pid):
                self.assertFalse(pc.valid_pid(pid))
                self.assertIsNone(pc.birth_identity(pid))

    def test_invalid_or_legacy_records_fail_closed(self):
        p = pc.record_path("worker")
        p.parent.mkdir(parents=True, exist_ok=True)
        for raw in (b'123',b'0',b'-1',b'null',b'{}',b'[]',b'"bad"',b'x'*5000):
            p.write_bytes(raw)
            with self.subTest(raw=raw[:12]), self.assertRaises(RuntimeError):
                pc.stop_owned("worker")
            self.assertTrue(p.exists())

    def test_nonregular_records_do_not_block(self):
        p = pc.record_path("worker")
        p.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "mkfifo"):
            os.mkfifo(p)
            with self.assertRaises(RuntimeError):
                pc.read_record("worker")
            p.unlink()
        p.mkdir()
        with self.assertRaises(RuntimeError):
            pc.read_record("worker")
        p.rmdir()

    def test_ready_stop_and_private_record(self):
        proc = pc.spawn("worker", self.command(), timeout=5)
        self.children.append(proc)
        r = pc.read_record("worker")
        self.assertEqual(r["state"], "ready")
        self.assertEqual(pc.owned_pid("worker"), proc.pid)
        self.assertEqual(pc.birth_identity(proc.pid), r["birth"])
        if os.name != "nt":
            self.assertEqual(pc.record_path("worker").stat().st_mode & 0o777, 0o600)
        self.assertTrue(pc.stop_owned("worker", timeout=3))
        proc.wait(timeout=3)
        self.assertFalse(pc.record_path("worker").exists())
        self.assertFalse(pc.stop_owned("worker"))

    def test_readiness_deadline_cleans_child_and_ignores_old_log(self):
        logs = Path(self.temp.name) / "logs"
        logs.mkdir()
        # A prior launch's valid line cannot satisfy this invocation's readiness.
        (logs / "worker.stdout.log").write_text(json.dumps({"event":"ready", "component":"bzs-worker", "host":"127.0.0.1", "port":12345,"version":__version__}) + "\n")
        marker = Path(self.temp.name) / "pid-marker"
        cmd = [sys.executable,"-c",f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(60)"]
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            pc.spawn("worker", cmd, timeout=0.5)
        self.assertLess(time.monotonic() - start, 6)
        self.assertFalse(pc.record_path("worker").exists())
        if marker.exists():
            self.assertIsNone(pc.birth_identity(int(marker.read_text())))

    def test_bad_readiness_and_oversize_are_cleaned(self):
        for body in ("not json", "x"*4097, json.dumps({"event":"ready", "port":True}),
                     json.dumps({"event":"ready","component":"foreign","host":"127.0.0.1","port":12345,"version":__version__})):
            cmd = [sys.executable,"-c",f"import time;print({body!r},flush=True);time.sleep(60)"]
            with self.subTest(body=body[:50]), self.assertRaises((ValueError, RuntimeError)):
                pc.spawn("worker", cmd, timeout=5)
            self.assertFalse(pc.record_path("worker").exists())

    def test_ready_followed_by_large_log_does_not_overflow_first_line(self):
        proc = pc.spawn("worker", self.command(after="print('x'*8192,flush=True);"), timeout=5)
        self.children.append(proc)
        self.assertEqual(pc.read_record("worker")["state"], "ready")
        self.assertTrue(pc.stop_owned("worker", timeout=3))

    def test_birth_mismatch_cannot_terminate_reused_pid(self):
        proc = pc.spawn("worker", self.command(), timeout=5)
        self.children.append(proc)
        r = pc.read_record("worker")
        r["birth"] += "-not-the-same-process"
        pc.record_path("worker").write_text(json.dumps(r))
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            pc.stop_owned("worker", timeout=0.05)
        self.assertIsNone(proc.poll())
        self.assertTrue(pc.record_path("worker").exists())

    def test_stop_failure_keeps_record(self):
        before = "signal.signal(signal.SIGTERM,signal.SIG_IGN);" if os.name != "nt" else ""
        proc = pc.spawn("worker", self.command(before=before), timeout=5)
        self.children.append(proc)
        record = pc.record_path("worker").read_bytes()
        if os.name == "nt":
            # Exercise failure classification without terminating another PID.
            birth = pc.birth_identity(proc.pid)
            self.assertIsNotNone(birth)
            # Keep the initial identity observation valid; deny only the
            # later termination handle, not the earlier read-only probe.
            with patch.object(pc, "birth_identity", return_value=birth), patch.object(pc, "_kernel") as get:
                get.return_value.OpenProcess.return_value = 0
                with self.assertRaisesRegex(RuntimeError, "termination handle"):
                    pc.stop_owned("worker", timeout=0.05)
        else:
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                pc.stop_owned("worker", timeout=0.05)
        self.assertEqual(pc.record_path("worker").read_bytes(), record)
        self.assertIsNone(proc.poll())

    def test_launcher_lock_is_exclusive_and_reusable(self):
        with pc.control_lock():
            with self.assertRaises(RuntimeError):
                with pc.control_lock():
                    self.fail("second operation acquired lock")
        with pc.control_lock():
            pass

    def test_duplicate_spawn_refuses_without_replacing_record(self):
        proc = pc.spawn("worker", self.command(), timeout=5)
        self.children.append(proc)
        data = pc.record_path("worker").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "already"):
            pc.spawn("worker", self.command(), timeout=5)
        self.assertEqual(pc.record_path("worker").read_bytes(), data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
