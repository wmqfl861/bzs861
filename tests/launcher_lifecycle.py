"""Dependency-free lifecycle checks, mandatory on Linux and Windows."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from bzs import __version__, process_control as pc


class Lifecycle(unittest.TestCase):
    # CL-25: a spawned child's inherited handle on logs\worker.log can survive
    # child.wait() for a moment under heavy parallel load, making the tearDown
    # TemporaryDirectory cleanup fail with a transient sharing violation
    # (WinError 32) after every assertion already passed. Retry that one
    # condition only; anything else propagates unchanged.
    _CLEANUP_BACKOFF_S = (0.05, 0.1, 0.2, 0.4, 0.5)  # then stay at 500 ms
    _CLEANUP_BUDGET_S = 5.0  # from the first attempt, time.monotonic()

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
        self._cleanup_temp_with_winerror32_retry()

    def _cleanup_temp_with_winerror32_retry(self):
        """self.temp.cleanup() with a bounded retry for transient WinError 32.

        First attempt immediately; on a Windows PermissionError with
        winerror==32 back off 50/100/200/400/500 ms then stay at 500 ms,
        inside a 5 s time.monotonic() budget with every wait clipped to the
        remaining budget and no retry after the deadline. Budget exhaustion
        stays a test ERROR: the last exception is chained, and the test name,
        held path, attempt count and elapsed time are recorded. Never
        warn-and-pass; ignore_cleanup_errors is never used.
        """
        start = time.monotonic()
        deadline = start + self._CLEANUP_BUDGET_S
        attempt = 0
        while True:
            attempt += 1
            try:
                self.temp.cleanup()
                return
            except PermissionError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) != 32:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"{self.id()}: temp-dir cleanup still blocked after {attempt} attempts "
                        f"in {time.monotonic() - start:.2f}s; held path: "
                        f"{exc.filename or self.temp.name}"
                    ) from exc
                delay = (self._CLEANUP_BACKOFF_S[attempt - 1]
                         if attempt <= len(self._CLEANUP_BACKOFF_S)
                         else self._CLEANUP_BACKOFF_S[-1])
                wait = min(delay, remaining)
                print(f"[launcher-lifecycle] temp cleanup retry: test={self.id()} "
                      f"attempt={attempt} winerror=32 path={exc.filename or self.temp.name!r} "
                      f"wait_ms={wait * 1000:.0f}", file=sys.stderr)
                time.sleep(wait)

    def command(self, before="", ready=None, after=""):
        ready = ready or {"event":"ready", "component":"bzs-worker", "host":"127.0.0.1", "port":12345, "version":__version__}
        return [sys.executable, "-c", "import os,time,json,signal;" + before + f"print({json.dumps(ready)!r},flush=True);" + after + "time.sleep(60)"]

    # ---- CL-25 signature-3 forensics (instrumentation only) -----------------
    # Everything below is gated on BZS_TEST_LIFECYCLE_DUMP=<dir>. Gate off:
    # zero effect on timing, output or pass/fail. Gate on: the signature-3
    # test caches test-side samples; when (and only when) it FAILS, an
    # observation-only window (<=20 s ceiling, no rescue kills, no green-
    # saving retries; the 5 s failure verdict stays unchanged) precedes one
    # JSON dump of the pre-cached cleanup events plus the fields below.

    _FORENSICS_ENV = "BZS_TEST_LIFECYCLE_DUMP"

    @classmethod
    def _forensics_on(cls) -> bool:
        return bool(os.environ.get(cls._FORENSICS_ENV))

    @classmethod
    def _forensics_exception_kind(cls, spawn_exc, events):
        text = f"{type(spawn_exc).__name__}: {spawn_exc}" if spawn_exc is not None else ""
        if not text:
            for event in reversed(events):
                if event.get("kind") == "spawn_abort":
                    text = f"{event.get('exc_type')}: {event.get('exc_str')}"
                    break
        if "readiness deadline exceeded" in text:
            return "readiness-deadline"
        if "child cleanup incomplete" in text or "stop not confirmed" in text:
            return "cleanup-deadline/incomplete"
        if "exited before readiness" in text or "exited during readiness" in text:
            return "early-exit"
        if "invalid readiness identity" in text or "port mismatch" in text:
            return "readiness-invalid"
        if "oversized readiness record" in text:
            return "readiness-oversize"
        return "other" if text else "unknown (no spawn exception captured)"

    def _forensics_image_path(self, pid):
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                 ctypes.POINTER(w.DWORD)]
        k.QueryFullProcessImageNameW.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return {"open_error": ctypes.get_last_error()}
        try:
            size = w.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return {"query_error": ctypes.get_last_error()}
            return {"path": buf.value}
        finally:
            k.CloseHandle(h)

    def _forensics_table(self):
        try:
            entries, _ = pc._RealWinApi().snapshot()
        except OSError as exc:
            return None, f"OSError: {exc}"
        return {pid: (parent, name) for pid, (parent, name) in entries.items()}, None

    def _forensics_process_detail(self, role, pid, table):
        info = {"role": role, "pid": pid}
        try:
            info["birth_now"] = pc.birth_identity(pid)
        except OSError as exc:
            info["birth_now"] = f"error: {exc}"
        info["image"] = self._forensics_image_path(pid)
        info["table_entry_now"] = ([pid, *table[pid]] if table and pid in table else None)
        return info

    def _forensics_observe(self, role_pids, ceiling_s=20.0):
        """Observation-only window (ruling 7): sample identities, never signal."""
        started = time.monotonic()
        out = {"ceiling_s": ceiling_s, "started_t": round(started, 6), "samples": []}
        pending = {pid for _, pid in role_pids}
        roles = {pid: role for role, pid in role_pids}
        while pending and time.monotonic() - started < ceiling_s:
            for pid in sorted(pending):
                try:
                    value = pc.birth_identity(pid)
                except OSError as exc:
                    value = f"error: {exc}"
                out["samples"].append({"t": round(time.monotonic(), 6), "pid": pid,
                                       "role": roles.get(pid, "?"), "birth": value})
                if value is None:
                    pending.discard(pid)
            if pending:
                time.sleep(0.25)
        out["duration_s"] = round(time.monotonic() - started, 6)
        out["still_live_at_end"] = sorted(pending)
        return out

    def _forensics_load(self):
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32")

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", w.DWORD), ("dwMemoryLoad", w.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        k.GlobalMemoryStatusEx(ctypes.byref(mem))

        def cpu_times():
            idle, kern, user = w.FILETIME(), w.FILETIME(), w.FILETIME()
            if not k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                raise OSError("GetSystemTimes failed")
            def ft(x):
                return (x.dwHighDateTime << 32) | x.dwLowDateTime
            return ft(idle), ft(kern), ft(user)
        idle1, kern1, user1 = cpu_times()
        time.sleep(0.2)
        idle2, kern2, user2 = cpu_times()
        d_idle, d_kern, d_user = idle2 - idle1, kern2 - kern1, user2 - user1
        total = d_kern + d_user  # kernel time already includes idle
        load = {"memory_load_pct": mem.dwMemoryLoad,
                "avail_phys_mb": round(mem.ullAvailPhys / 2**20, 1),
                "total_phys_mb": round(mem.ullTotalPhys / 2**20, 1),
                "cpu_idle_delta": d_idle, "cpu_kernel_delta": d_kern,
                "cpu_user_delta": d_user,
                "cpu_busy_fraction_sample": (round(max(0, total - d_idle) / total, 4)
                                             if total else None)}
        table, table_error = self._forensics_table()
        if table_error:
            load["table_error"] = table_error
        else:
            names = [name for _, (_, name) in table.items()]
            load["total_processes"] = len(names)
            load["python_processes"] = sum(1 for n in names if "python" in n.lower())
        return load

    def _forensics_capture_signature3(self, failure, spawn_exc, file_seen, samples, pid_paths):
        """Dump pre-failure cleanup events plus test-side evidence.

        Observation-only first (no signals, no retries), then one JSON file in
        the gate directory. A bug here must never mask the real failure.
        """
        try:
            all_events = pc._forensics_events()
            mark_index = max((i for i, e in enumerate(all_events)
                              if e.get("kind") == "mark" and e.get("label") == self.id()),
                             default=0)
            events = all_events[mark_index:]
            roles = {}
            stub_pid = None
            for event in events:
                if event.get("kind") == "spawn_start" and stub_pid is None:
                    stub_pid = event.get("pid")
            if stub_pid:
                roles["stub"] = stub_pid
            for role in ("real", "leaf"):
                try:
                    if pid_paths[role].exists():
                        roles[role] = int(pid_paths[role].read_text())
                except (OSError, ValueError):
                    pass
            observation = self._forensics_observe(sorted(roles.items()))
            table, table_error = self._forensics_table()
            processes = [self._forensics_process_detail(role, pid, table)
                         for role, pid in sorted(roles.items())]
            record_info = {"checked_t": round(time.monotonic(), 6)}
            try:
                record_info["exists"] = pc.record_path("worker").exists()
                if record_info["exists"]:
                    record_info["content"] = pc.record_path("worker").read_text()[:500]
            except OSError as exc:
                record_info["error"] = f"OSError: {exc}"
            dump = {
                "meta": {
                    "test": self.id(),
                    "run": os.environ.get("BZS_TEST_LIFECYCLE_RUN"),
                    "commit": os.environ.get("BZS_TEST_LIFECYCLE_COMMIT"),
                    "gate_env": self._FORENSICS_ENV,
                    "dump_dir": os.environ.get(self._FORENSICS_ENV),
                    "python_pid": os.getpid(),
                    "python_executable": sys.executable,
                    "python_base_executable": getattr(sys, "_base_executable", None),
                    "python_version": sys.version,
                    "bzs_module": getattr(pc, "__file__", None),
                    "generated_wall": time.time(),
                    "event_count": len(events),
                },
                "failure": {"type": type(failure).__name__, "str": str(failure),
                            "traceback_tail": traceback.format_exception(failure)[-8:]},
                "spawn_exception": ({"type": type(spawn_exc).__name__, "str": str(spawn_exc),
                                     "chain": pc._forensics_chain(spawn_exc)}
                                    if spawn_exc is not None else None),
                "exception_kind": self._forensics_exception_kind(spawn_exc, events),
                "pid_file_first_seen": dict(file_seen),
                "test_death_check_samples": list(samples),
                "failing_sample": (samples[-1] if samples else None),
                "observation": observation,
                "processes": processes,
                "table_error": table_error,
                "record_state": record_info,
                "load": self._forensics_load(),
                "events": events,
            }
            target = Path(os.environ.get(self._FORENSICS_ENV))
            target.mkdir(parents=True, exist_ok=True)
            name = (f"sig3-run{os.environ.get('BZS_TEST_LIFECYCLE_RUN', 'na')}-"
                    f"{int(time.time())}.json")
            (target / name).write_text(json.dumps(dump, indent=1, default=str),
                                       encoding="utf-8")
            print(f"[lifecycle-forensics] dump written: {target / name}", file=sys.stderr)
        except Exception as dump_error:  # diagnostics must not mask the failure
            print(f"[lifecycle-forensics] dump failed: {type(dump_error).__name__}: "
                  f"{dump_error}", file=sys.stderr)


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
        # CL-25 signature-3 forensics: everything in `if forensics:` blocks is
        # recording only; the 5 s deadline, the loop structure and every
        # assertion (the failure verdict) are unchanged. Gate off: identical
        # behavior to the uninstrumented test.
        forensics = self._forensics_on()
        spawn_exc = None
        file_seen = {}
        samples = []
        if forensics:
            pc._forensics_mark(self.id())
        try:
            with self.assertRaisesRegex(RuntimeError, "deadline") as raised:
                pc.spawn("worker", cmd, timeout=0.5)
            spawn_exc = raised.exception
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (real.exists() and leaf.exists()):
                if forensics:
                    for role, path in (("real", real), ("leaf", leaf)):
                        if role not in file_seen and path.exists():
                            file_seen[role] = round(time.monotonic(), 6)
                time.sleep(0.05)
            if forensics:
                for role, path in (("real", real), ("leaf", leaf)):
                    if role not in file_seen and path.exists():
                        file_seen[role] = round(time.monotonic(), 6)
            pids = []
            for path in (real, leaf):
                if path.exists():
                    pids.append(int(path.read_text()))
            for pid in pids:
                while time.monotonic() < deadline:
                    if pc.birth_identity(pid) is None:
                        break
                    time.sleep(0.1)
                value = pc.birth_identity(pid)
                if forensics:
                    samples.append({"pid": pid, "t": round(time.monotonic(), 6),
                                    "budget_left_s": round(deadline - time.monotonic(), 6),
                                    "birth": value})
                self.assertIsNone(value)
            self.assertFalse(pc.record_path("worker").exists())
        except AssertionError as failure:
            # Observation-only forensics (ruling 7): <=20 s ceiling after the
            # unchanged 5 s verdict, no rescue kills, no retries; then dump.
            if forensics:
                self._forensics_capture_signature3(failure, spawn_exc, file_seen,
                                                   samples, {"real": real, "leaf": leaf})
            raise

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
