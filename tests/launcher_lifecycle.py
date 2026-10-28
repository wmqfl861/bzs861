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

    def test_windows_cleanup_kills_the_interpreter_tree(self):
        if os.name != "nt":
            self.skipTest("Windows venv launcher tree")
        # sys.executable in a venv is a launcher stub; the -c code runs in its
        # child, which here spawns a third sleeper. Cleanup of this invocation
        # must take down the whole chain, not only the launcher pid.
        real = Path(self.temp.name) / "real.pid"
        leaf = Path(self.temp.name) / "leaf.pid"
        inner = (f"import os,subprocess,sys,time;"
                 f"open({str(real)!r},'w').write(str(os.getpid()));"
                 f"subprocess.Popen([sys._base_executable,'-c',"
                 f"\"import os,time;open({str(leaf)!r},'w').write(str(os.getpid()));time.sleep(60)\"]);"
                 f"time.sleep(60)")
        cmd = [sys.executable, "-c", inner]
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            pc.spawn("worker", cmd, timeout=0.5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (real.exists() and leaf.exists()):
            time.sleep(0.05)
        pids = []
        for path in (real, leaf):
            if path.exists():
                pids.append(int(path.read_text()))
        for pid in pids:
            while time.monotonic() < deadline:
                if pc.birth_identity(pid) is None:
                    break
                time.sleep(0.1)
            self.assertIsNone(pc.birth_identity(pid))
        self.assertFalse(pc.record_path("worker").exists())

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
        if os.name == "nt" and r["birth"].startswith("win:"):
            # A different but well-formed creation time: a pid now held by
            # another instance must abort the whole stop.
            r["birth"] = f"win:{int(r['birth'][4:]) + 1}"
        else:
            r["birth"] += "-not-the-same-process"
        pc.record_path("worker").write_text(json.dumps(r))
        with self.assertRaisesRegex(RuntimeError, "identity changed|unverified"):
            pc.stop_owned("worker", timeout=0.05)
        self.assertIsNone(proc.poll())
        self.assertTrue(pc.record_path("worker").exists())

    def test_stop_failure_keeps_record(self):
        before = "signal.signal(signal.SIGTERM,signal.SIG_IGN);" if os.name != "nt" else ""
        proc = pc.spawn("worker", self.command(before=before), timeout=5)
        self.children.append(proc)
        record = pc.record_path("worker").read_bytes()
        if os.name == "nt":
            # Exercise failure classification without terminating anything:
            # every open in the verified-tree walk is denied.
            class DeniedApi(pc._RealWinApi):
                def open(self, pid):
                    raise pc._ProcessDenied(pid)
            with patch.object(pc, "_RealWinApi", DeniedApi):
                with self.assertRaisesRegex(RuntimeError, "not confirmed"):
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
