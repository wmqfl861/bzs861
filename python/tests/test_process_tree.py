"""Deterministic attribution and failure tests for the Windows process-tree stopper.

The fake API scripts exact process-table states and scheduling outcomes so the
races that matter (PID reuse, stale parent links, access denial, termination
failures, bounded budgets) are reproducible on demand. Real-process tests at
the bottom prove the same properties against actual Windows processes created
by this test alone; nothing outside this test is ever signalled.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bzs import process_control as pc

# ---------------------------------------------------------------- fake API


class FakeProc:
    def __init__(self, pid, ppid, creation, *, alive=True, denies_access=False):
        self.pid, self.ppid, self.creation = pid, ppid, creation
        self.alive, self.denies_access = alive, denies_access
        self.reaped = False
        self.terminate_requested = False
        self.handle = object()


class FakeWinApi:
    """Deterministic Windows process API with a scriptable table and clock.

    Mirrors the real semantics: TerminateProcess only *requests* termination;
    only a successful wait observes the exit.
    """

    def __init__(self, table, *, now_ft=10_000, fails_snapshot=False,
                 fails_enum=False, wait_outcome="signaled", terminate_fail_pids=()):
        self.table = {p.pid: p for p in table}      # pid -> FakeProc
        self.now_ft = now_ft
        self.fails_snapshot = fails_snapshot
        self.fails_enum = fails_enum
        self.wait_outcome = wait_outcome            # "signaled"|"timeout"
        self.terminate_fail_pids = set(terminate_fail_pids)
        self.snapshots = 0
        self.closed = []
        self.terminate_order = []

    def snapshot(self):
        self.snapshots += 1
        if self.fails_snapshot:
            raise OSError("snapshot failed")
        if self.fails_enum:
            raise OSError("enumeration failed mid-table")
        return {pid: p.ppid for pid, p in self.table.items()}, self.now_ft

    def open(self, pid):
        p = self.table.get(pid)
        if p is None or p.reaped:
            raise pc._ProcessGone(pid)
        if p.denies_access:
            raise pc._ProcessDenied(pid)
        return p.handle

    def _by_handle(self, handle):
        for p in self.table.values():
            if p.handle is handle:
                return p
        raise AssertionError("unknown handle")

    def creation(self, handle):
        return self._by_handle(handle).creation

    def exited(self, handle):
        return not self._by_handle(handle).alive

    def terminate(self, handle):
        p = self._by_handle(handle)
        self.terminate_order.append(p.pid)
        if p.pid in self.terminate_fail_pids:
            return False
        p.terminate_requested = True
        return True

    def wait(self, handle, ms):
        p = self._by_handle(handle)
        if self.wait_outcome == "timeout":
            return "timeout"
        if p.terminate_requested or not p.alive:
            p.alive = False
            return "signaled"
        return "timeout"

    def close(self, handle):
        self.closed.append(self._by_handle(handle).pid)


def tree(root_pid, root_creation, api, **kw):
    return pc._win_terminate_tree(root_pid, root_creation, api=api, **kw)


def survivors(api, *pids):
    return [p for p in api.table.values() if p.pid in pids and p.alive]


ROOT = 100
ROOT_CREATION = 1_000


def api_with_stub_chain():
    """venv launcher stub -> real interpreter -> sleeper, all alive."""
    return FakeWinApi([
        FakeProc(ROOT, 1, ROOT_CREATION),          # launcher stub (root)
        FakeProc(200, ROOT, 2_000),                # real interpreter
        FakeProc(300, 200, 3_000),                 # interpreter's child
        FakeProc(999, 1, 900),                     # unrelated process
    ])


class TestAttribution:
    def test_three_layer_tree_is_terminated_deepest_first(self):
        api = api_with_stub_chain()
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed == []
        assert api.terminate_order == [300, 200, 100]
        assert all(not p.alive for p in api.table.values() if p.pid != 999)
        assert api.table[999].alive

    def test_reused_descendant_pid_after_snapshot_is_not_signalled(self):
        # The original child dies and an unrelated process reuses its pid
        # after the snapshot: a creation time later than the snapshot must
        # reject it, and only the verified root is still terminated.
        api = api_with_stub_chain()
        real_snapshot = api.snapshot

        def snapshot():
            parents, ft = real_snapshot()
            api.table[200].alive = False
            api.table[200].reaped = True
            api.table[200] = FakeProc(200, 555, ft + 5_000)  # reuse, new parent
            return parents, ft
        api.snapshot = snapshot
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert api.table[200].alive and 200 not in api.terminate_order
        assert any("200" in u for u in unconfirmed)
        assert api.terminate_order == [ROOT]

    def test_historical_orphan_with_matching_ppid_survives(self):
        # An orphan created long ago whose ppid happens to equal the new root
        # pid: created BEFORE the root instance, so it cannot be its child.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 500),               # created before the root
            FakeProc(300, ROOT, 2_000),             # genuine child
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert api.table[200].alive and 200 not in api.terminate_order
        assert api.terminate_order == [300, ROOT]

    def test_dead_root_never_signals_and_reports_candidates(self):
        # Root already gone: children claiming ppid==root cannot be bound to
        # this root instance (the pid may have been recycled before it died),
        # so nothing may be signalled and the candidates are reported.
        api = FakeWinApi([
            FakeProc(200, ROOT, 2_000),
            FakeProc(300, 200, 3_000),
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert api.terminate_order == []
        assert all(p.alive for p in api.table.values())
        assert unconfirmed

    def test_reused_middle_parent_does_not_capture_old_grandchildren(self):
        # Old middle 200 died; an unrelated process reused pid 200 with its
        # own parent elsewhere. The old grandchild (ppid 200) must not be
        # attributed through the new 200.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, 1, 9_000),                # reused, unrelated parent
            FakeProc(300, 200, 3_500),              # old grandchild of dead 200
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert 300 not in api.terminate_order and 200 not in api.terminate_order
        assert api.terminate_order == [ROOT]

    def test_access_denied_target_is_reported_not_killed(self):
        api = api_with_stub_chain()
        api.table[200].denies_access = True
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("200" in u for u in unconfirmed)
        # The denied middle breaks the chain: its subtree is unverifiable, so
        # only the verified root is signalled.
        assert api.terminate_order == [ROOT]
        assert api.table[300].alive

    def test_root_birth_mismatch_aborts_everything(self):
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION + 999),  # a different instance
            FakeProc(200, ROOT, 2_000),
        ])
        with pytest.raises(RuntimeError, match="identity changed"):
            tree(ROOT, ROOT_CREATION, api)
        assert api.terminate_order == []


class TestFailureReporting:
    def test_snapshot_failure_never_signals_anything(self):
        api = api_with_stub_chain()
        api.fails_snapshot = True
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed and api.terminate_order == []

    def test_enumeration_failure_never_signals_anything(self):
        api = api_with_stub_chain()
        api.fails_enum = True
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed and api.terminate_order == []

    def test_terminate_failure_reports_and_others_still_processed(self):
        api = api_with_stub_chain()
        api.terminate_fail_pids = {200}
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("200" in u for u in unconfirmed)
        assert api.terminate_order == [300, 200, 100]

    def test_wait_timeout_reports_every_unconfirmed_target(self):
        api = api_with_stub_chain()
        api.wait_outcome = "timeout"
        unconfirmed = tree(ROOT, ROOT_CREATION, api, deadline_s=0.05)
        assert len(unconfirmed) == 3

    def test_total_budget_bounds_the_wait(self):
        api = api_with_stub_chain()
        api.wait_outcome = "timeout"
        started = time.monotonic()
        unconfirmed = tree(ROOT, ROOT_CREATION, api, deadline_s=0.2)
        assert time.monotonic() - started < 1.5
        assert len(unconfirmed) == 3

    def test_every_opened_handle_is_closed(self):
        api = api_with_stub_chain()
        tree(ROOT, ROOT_CREATION, api)
        assert set(api.closed) == {ROOT, 200, 300}
        api2 = api_with_stub_chain()
        api2.table[200].denies_access = True  # never opened, so never closed
        tree(ROOT, ROOT_CREATION, api2)
        assert set(api2.closed) == {ROOT}


# ------------------------------------------------------- record hygiene (C)


class TestRecordHygiene:
    def test_busy_control_lock_cleans_processes_but_keeps_records(self, tmp_path, monkeypatch):
        import contextlib, json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        record = {"version": 1, "name": "worker", "pid": 4321, "birth": "win:1",
                  "port": 8611, "state": "ready"}
        pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
        pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")

        @contextlib.contextmanager
        def busy():
            raise RuntimeError("another launcher operation is running")
            yield None
        monkeypatch.setattr(pc, "control_lock", busy)
        cleaned = []
        monkeypatch.setattr(pc, "cleanup_child",
                            lambda name, proc, **kw: cleaned.append((name, kw.get("forget"))))
        pc.cleanup_children([("worker", object())])
        assert cleaned == [("worker", False)]          # processes reclaimed...
        assert pc.record_path("worker").exists()       # ...shared record untouched

    def test_record_replacement_between_read_and_unlink_is_not_deleted(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        old = {"version": 1, "name": "worker", "pid": 111, "birth": "win:1",
               "port": 0, "state": "starting"}
        new = {"version": 1, "name": "worker", "pid": 222, "birth": "win:2",
               "port": 8611, "state": "ready"}
        path = pc.record_path("worker")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new), encoding="utf-8")
        pc._forget("worker", old)
        assert json.loads(path.read_text(encoding="utf-8")) == new


# ---------------------------------------------------------- real processes


def _read_pid(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not Path(path).exists():
        time.sleep(0.05)
    return int(Path(path).read_text())


@pytest.mark.skipif(os.name != "nt", reason="real Windows process tree")
class TestRealWindowsProcesses:
    def _spawn_chain(self, tmp_path):
        # Forward slashes: the leaf code string is re-parsed as source by the
        # grandchild, where backslash paths would become invalid escapes.
        leaf = str(Path(tmp_path) / "leaf.pid").replace("\\", "/")
        real = str(Path(tmp_path) / "real.pid").replace("\\", "/")
        inner = (f"import os,subprocess,sys,time;"
                 f"open({real!r},'w').write(str(os.getpid()));"
                 f"subprocess.Popen([sys._base_executable,'-c',"
                 f"\"import os,time;open({leaf!r},'w').write(str(os.getpid()));time.sleep(60)\"]);"
                 f"time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", inner],
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
        proc._bzs_birth = pc.birth_identity(proc.pid)
        return proc, _read_pid(real), _read_pid(leaf)

    def _gone(self, pid):
        return pc.birth_identity(pid) is None

    def test_stop_owned_kills_the_whole_real_tree(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        proc, real_pid, leaf_pid = self._spawn_chain(tmp_path)
        record = {"version": 1, "name": "worker", "pid": proc.pid,
                  "birth": proc._bzs_birth, "port": 8611, "state": "ready"}
        pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
        pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")
        try:
            assert pc.stop_owned("worker", timeout=10) is True
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (self._gone(real_pid) and self._gone(leaf_pid)):
            time.sleep(0.1)
        assert self._gone(real_pid) and self._gone(leaf_pid)
        assert not pc.record_path("worker").exists()

    def test_unrelated_process_survives_a_tree_stop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        bystander = subprocess.Popen([sys._base_executable, "-c", "import time;time.sleep(60)"],
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
        proc, real_pid, _leaf = self._spawn_chain(tmp_path)
        try:
            pc._win_terminate_tree(proc.pid, int(proc._bzs_birth.split(":")[1]), deadline_s=10)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        assert self._gone(real_pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self._gone(real_pid):
            time.sleep(0.1)
        assert self._gone(real_pid)
        assert bystander.poll() is None          # never touched by the tree stop
        bystander.kill()
        bystander.wait(timeout=5)

    def test_dead_root_candidates_surface_and_record_is_kept(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        # Plain parent->child pair (no venv stub): the child outlives its
        # killed parent, so the dead root still has a live candidate child.
        parent = subprocess.Popen(
            [sys._base_executable, "-c",
             "import subprocess,sys,time;"
             "print(subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(60)']).pid,flush=True);"
             "time.sleep(60)"],
            stdout=subprocess.PIPE, text=True)
        parent._bzs_birth = pc.birth_identity(parent.pid)
        child_pid = int(parent.stdout.readline().strip())
        parent.stdout.close()
        parent.kill()
        parent.wait(timeout=5)
        try:
            assert pc.birth_identity(child_pid) is not None  # child survives
            record = {"version": 1, "name": "worker", "pid": parent.pid,
                      "birth": parent._bzs_birth, "port": 8611, "state": "ready"}
            pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
            pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")
            # A completely dead root cannot bound its children's creation
            # window; candidates must surface and the record must be kept.
            with pytest.raises(RuntimeError):
                pc.stop_owned("worker", timeout=5)
            assert pc.record_path("worker").exists()
        finally:
            birth = pc.birth_identity(child_pid)
            if birth:
                pc._win_terminate_tree(child_pid, int(birth.split(":")[1]), deadline_s=10)
