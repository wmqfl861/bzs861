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


def await_original_identity_gone(query, anchor, deadline, *, now=time.monotonic,
                                 sleep=time.sleep):
    """Death-check verdict for one tree member pinned by an identity anchor.

    CL-25 signature 3 (codex ruling, fix round): the anchor is the member's
    ORIGINAL identity captured at write time. A sample of None (nothing holds
    the pid any more) OR a different valid identity (the pid was reused)
    confirms the original identity is gone; a sample equal to the anchor keeps
    waiting; an identity-query EXCEPTION never counts as an exit. The loop
    shares the caller's single deadline; one sample after the deadline decides
    the verdict. Returns (gone, last) where `last` is the deciding sample or
    the deciding OSError.
    """
    gone = False
    last = None
    while now() < deadline:
        try:
            last = query()
        except OSError as exc:
            last = exc  # a query failure is not an exit; keep the window
        else:
            if last is None or last != anchor:
                gone = True
                break
        sleep(0.1)
    if not gone:
        try:
            last = query()
        except OSError as exc:
            last = exc
        gone = last is None or (not isinstance(last, OSError) and last != anchor)
    return gone, last


def read_identity_anchor(role, path):
    """Read one write-time anchor file "<pid> <birth_identity>" (ruling 3).

    A file that exists but lacks or corrupts its identity anchor is an ERROR;
    an absent anchor must never fabricate success (never-appearing files are
    skipped by the caller, keeping the existing handling).
    """
    try:
        raw = path.read_text()
    except OSError as exc:
        raise RuntimeError(f"{role} pid file {path} exists but cannot be read: {exc}") from exc
    fields = raw.split()
    if len(fields) != 2 or not fields[0].isdigit() or not fields[1]:
        raise RuntimeError(f"{role} pid file {path} exists but lacks a valid "
                           f"identity anchor: {raw[:64]!r}")
    pid = int(fields[0])
    if not pc.valid_pid(pid):
        raise RuntimeError(f"{role} pid file {path} anchors invalid pid {pid}")
    return pid, fields[1]


def collect_identity_anchors(paths):
    """Merge existing anchor files into {pid: original birth identity}.

    `paths` is an ordered sequence of (role, path). Missing files are skipped
    (a member that never wrote its anchor cannot be death-checked); the same
    pid anchored twice must carry the same original identity.
    """
    anchors: "dict[int, str]" = {}
    for role, path in paths:
        if not path.exists():
            continue
        pid, birth = read_identity_anchor(role, path)
        previous = anchors.setdefault(pid, birth)
        if previous != birth:
            raise RuntimeError(f"conflicting identity anchors for pid {pid}: "
                               f"{previous!r} vs {birth!r}")
    return anchors


class Signature3DeathCheck(unittest.TestCase):
    """Deterministic regressions for the CL-25 signature-3 fix (ruling 5).

    test_windows_cleanup_kills_the_interpreter_tree delegates its per-member
    verdict to await_original_identity_gone with write-time anchors. These
    regressions inject fake identity samplers (and a fake clock) so every
    verdict rule is exercised deterministically, with no real pid churn.
    """

    @staticmethod
    def _clock(start=100.0):
        state = {"t": start}
        return ((lambda: state["t"]),
                (lambda seconds: state.__setitem__("t", state["t"] + seconds)))

    @staticmethod
    def _sticky(samples):
        """Sampler yielding `samples` in order, then repeating the last one."""
        state = {"i": 0}
        def query():
            i = min(state["i"], len(samples) - 1)
            state["i"] += 1
            value = samples[i]
            if isinstance(value, Exception):
                raise value
            return value
        return query

    def test_original_identity_persistently_alive_fails(self):
        # Still the anchored identity at the deadline MUST fail.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor"]), "win:anchor", 105.0, now=now, sleep=sleep)
        self.assertFalse(gone)
        self.assertEqual(last, "win:anchor")

    def test_pid_reuse_different_identity_passes(self):
        # run79 signature: the pid is later held by a DIFFERENT process
        # object; a different valid identity confirms the original is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor", "win:anchor", "win:reuser"]),
            "win:anchor", 105.0, now=now, sleep=sleep)
        self.assertTrue(gone)
        self.assertEqual(last, "win:reuser")

    def test_reuse_before_first_sample_passes(self):
        # The pid got reused BEFORE the file was read: the first query
        # already reports the reuser while the anchor keeps the write-time
        # identity. This is exactly what a post-hoc anchor would miss.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:reuser"]), "win:anchor", 105.0, now=now, sleep=sleep)
        self.assertTrue(gone)
        self.assertEqual(last, "win:reuser")

    def test_none_sample_passes(self):
        # Nothing holds the pid any more: the original identity is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor", None]), "win:anchor", 105.0,
            now=now, sleep=sleep)
        self.assertTrue(gone)
        self.assertIsNone(last)

    def test_identity_query_error_is_not_an_exit(self):
        # Query exceptions are neither an exit nor a failure; the verdict
        # only lands on a later None/different-identity sample.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed"), OSError("probe failed"), None]),
            "win:anchor", 105.0, now=now, sleep=sleep)
        self.assertTrue(gone)
        self.assertIsNone(last)

    def test_identity_query_error_at_deadline_does_not_pass(self):
        # Only exceptions until the deadline: the original was never
        # confirmed gone, so the verdict must not pass.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed")]), "win:anchor", 105.0,
            now=now, sleep=sleep)
        self.assertFalse(gone)
        self.assertIsInstance(last, OSError)

    def test_anchor_file_without_identity_anchor_errors(self):
        # Ruling 3: a pid file that exists but lacks/corrupts its identity
        # anchor must ERROR; an absent anchor never fabricates success.
        with tempfile.TemporaryDirectory(prefix="bzs-anchor-") as tmp:
            good = Path(tmp) / "good.pid"
            good.write_text("924 win:134390212197500154\n")
            self.assertEqual(read_identity_anchor("real", good),
                             (924, "win:134390212197500154"))
            for raw in ("924", "924 ", "924 win:1 extra", "nine win:1",
                        "0 win:1", "1 win:1", "-4 win:1", "", "win:1"):
                path = Path(tmp) / "bad.pid"
                path.write_text(raw)
                with self.subTest(raw=raw[:20]), self.assertRaises(RuntimeError):
                    read_identity_anchor("real", path)

    def test_anchor_collection_skips_missing_and_refuses_conflicts(self):
        with tempfile.TemporaryDirectory(prefix="bzs-anchor-") as tmp:
            absent = Path(tmp) / "never-written.pid"
            stub = Path(tmp) / "stub.pid"
            real = Path(tmp) / "real.pid"
            self.assertEqual(collect_identity_anchors(
                (("stub", absent), ("real", absent))), {})
            # Same pid, same original identity from two roles: checked once.
            stub.write_text("924 win:same\n")
            real.write_text("924 win:same\n")
            self.assertEqual(collect_identity_anchors(
                (("stub", stub), ("real", real))), {924: "win:same"})
            real.write_text("924 win:other\n")
            with self.assertRaisesRegex(RuntimeError, "conflicting"):
                collect_identity_anchors((("stub", stub), ("real", real)))
            # A missing member is skipped; present members are still checked.
            self.assertEqual(collect_identity_anchors(
                (("stub", stub), ("leaf", absent))), {924: "win:same"})


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
            for role in ("stub", "real", "leaf"):
                try:
                    if pid_paths[role].exists():
                        # Anchored pid files: "<pid> <birth_identity>".
                        roles[role] = read_identity_anchor(role, pid_paths[role])[0]
                except (OSError, ValueError, RuntimeError):
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
        stub = Path(self.temp.name) / "stub.pid"
        # CL-25 signature-3 fix (codex ruling (a')): identity anchors are
        # captured AT WRITE TIME, never reconstructed after cleanup. The
        # real/leaf writers atomically write "<pid> <birth_identity(pid)>"
        # into their pid files while alive, so a pid reused later by another
        # process (the run79 false positive: a churn reuser held the dead
        # member's pid at the death check) reads as a DIFFERENT identity and
        # confirms the original is gone. The stub/root anchor is the ORIGINAL
        # identity spawn() recorded in the run record at spawn time, copied
        # out by the inner code before cleanup deletes that record.
        repo_python = str(Path(__file__).resolve().parents[1] / "python")
        anchor_writer = (
            "import os\n"
            "def anchor(path, pid, birth):\n"
            "    tmp = path + '.anchor.tmp'\n"
            "    with open(tmp, 'w') as fh:\n"
            "        fh.write(f'{pid} {birth}')\n"
            "    os.replace(tmp, path)\n"
        )
        leaf_code = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {repo_python!r})\n"
            "import bzs.process_control as pc\n"
            + anchor_writer +
            f"anchor({str(leaf)!r}, os.getpid(), pc.birth_identity(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        inner = (
            "import os, subprocess, sys, time\n"
            f"sys.path.insert(0, {repo_python!r})\n"
            "import bzs.process_control as pc\n"
            + anchor_writer +
            f"anchor({str(real)!r}, os.getpid(), pc.birth_identity(os.getpid()))\n"
            f"subprocess.Popen([sys._base_executable, '-c', {leaf_code!r}])\n"
            "stub_record = None\n"
            "stub_end = time.monotonic() + 2\n"
            "while stub_record is None and time.monotonic() < stub_end:\n"
            "    try:\n"
            "        stub_record = pc.read_record('worker')\n"
            "    except Exception:\n"
            "        stub_record = None\n"
            "    if stub_record is None:\n"
            "        time.sleep(0.02)\n"
            "if stub_record is not None:\n"
            f"    anchor({str(stub)!r}, stub_record['pid'], stub_record['birth'])\n"
            "time.sleep(60)\n"
        )
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
                    for role, path in (("real", real), ("leaf", leaf), ("stub", stub)):
                        if role not in file_seen and path.exists():
                            file_seen[role] = round(time.monotonic(), 6)
                time.sleep(0.05)
            if forensics:
                for role, path in (("real", real), ("leaf", leaf), ("stub", stub)):
                    if role not in file_seen and path.exists():
                        file_seen[role] = round(time.monotonic(), 6)
            # Ruling 3: an existing pid file without a valid anchor ERRORs;
            # never-appearing files keep the existing skip handling.
            anchors = collect_identity_anchors(
                (("stub", stub), ("real", real), ("leaf", leaf)))
            if forensics:
                print(f"[lifecycle-forensics] signature3 anchors: "
                      f"{sorted(anchors.items())} file_seen={sorted(file_seen)}",
                      file=sys.stderr)
            for pid, anchor in sorted(anchors.items()):
                gone, last = await_original_identity_gone(
                    lambda pid=pid: pc.birth_identity(pid), anchor, deadline)
                if forensics:
                    samples.append({"pid": pid, "anchor": anchor,
                                    "t": round(time.monotonic(), 6),
                                    "budget_left_s": round(deadline - time.monotonic(), 6),
                                    "birth": (None if isinstance(last, OSError) else last),
                                    "query_error": (str(last) if isinstance(last, OSError)
                                                    else None)})
                if not gone:
                    if isinstance(last, OSError):
                        self.fail(f"pid {pid}: original identity {anchor!r} not confirmed "
                                  f"gone; identity query failed at deadline: {last}")
                    self.fail(f"pid {pid}: original identity {anchor!r} still alive at "
                              f"deadline (last sample {last!r})")
            self.assertFalse(pc.record_path("worker").exists())
        except AssertionError as failure:
            # Observation-only forensics (ruling 7): <=20 s ceiling after the
            # unchanged 5 s verdict, no rescue kills, no retries; then dump.
            if forensics:
                self._forensics_capture_signature3(
                    failure, spawn_exc, file_seen, samples,
                    {"stub": stub, "real": real, "leaf": leaf})
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
