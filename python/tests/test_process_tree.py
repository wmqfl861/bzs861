"""Deterministic attribution and failure tests for the Windows process-tree stopper.

The fake API scripts exact process-table states and scheduling outcomes so the
races that matter (PID reuse, stale parent links, access denial, termination
failures, bounded budgets) are reproducible on demand. Real-process tests at
the bottom prove the same properties against actual Windows processes created
by this test alone; nothing outside this test is ever signalled.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from bzs import process_control as pc

# ---------------------------------------------------------------- fake API


class FakeProc:
    def __init__(self, pid, ppid, creation, *, alive=True, denies_access=False,
                 denies_terminate_access=False, name="stub.exe"):
        self.pid, self.ppid, self.creation = pid, ppid, creation
        self.alive, self.denies_access = alive, denies_access
        self.denies_terminate_access = denies_terminate_access
        self.name = name
        self.reaped = False
        self.terminate_requested = False
        self.query_handle = object()
        self.terminate_handle = object()


class FakeWinApi:
    """Deterministic Windows process API with a scriptable table and clock.

    Mirrors the real semantics: TerminateProcess only *requests* termination;
    only a successful wait observes the exit. The two-step open is mirrored
    too: `open` grants query+synchronize, `open_terminate` grants terminate
    and may be denied independently.
    """

    def __init__(self, table, *, now_ft=10_000, fails_snapshot=False,
                 fails_enum=False, fails_handleless=False, wait_outcome="signaled",
                 terminate_fail_pids=()):
        self.table = {p.pid: p for p in table}      # pid -> FakeProc
        self.now_ft = now_ft
        self.fails_snapshot = fails_snapshot
        self.fails_enum = fails_enum
        self.fails_handleless = fails_handleless
        self.wait_outcome = wait_outcome            # "signaled"|"timeout"
        self.terminate_fail_pids = set(terminate_fail_pids)
        self.snapshots = 0
        self.closed = []
        self.closed_handles = []
        self.terminate_order = []
        self.terminate_opens = []
        self.handleless_queries = []

    def snapshot(self):
        self.snapshots += 1
        if self.fails_snapshot:
            raise OSError("snapshot failed")
        if self.fails_enum:
            raise OSError("enumeration failed mid-table")
        return {pid: (p.ppid, p.name) for pid, p in self.table.items()}, self.now_ft

    def open(self, pid):
        p = self.table.get(pid)
        if p is None or p.reaped:
            raise pc._ProcessGone(pid)
        if p.denies_access:
            raise pc._ProcessDenied(pid)
        return p.query_handle

    def open_terminate(self, pid):
        self.terminate_opens.append(pid)
        p = self.table.get(pid)
        if p is None or p.reaped:
            raise pc._ProcessGone(pid)
        if p.denies_terminate_access:
            raise pc._ProcessDenied(pid)
        return p.terminate_handle

    def creation_without_handle(self, pid):
        self.handleless_queries.append(pid)
        if self.fails_handleless:
            return None
        p = self.table.get(pid)
        return p.creation if p is not None else None

    def _by_handle(self, handle):
        for p in self.table.values():
            if p.query_handle is handle or p.terminate_handle is handle:
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
        self.closed_handles.append(handle)
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


# --------------------------------------- stale-parent exclusion (CL-25)


class TestStaleParentExclusion:
    """A snapshot entry whose ppid names an already-dead process is often a
    stranger reusing the pid: Windows never updates a child's recorded ppid.
    Entries created strictly before their nominal parent cannot be members;
    they are excluded outright instead of failing the stop as unconfirmed."""

    def test_a_stale_parent_reused_pid_open_denied_is_excluded_and_never_terminated(self):
        # (a) old orphan, ppid == root, query open denied (e.g. a SYSTEM
        # process): the handle-less creation lookup proves it predates the
        # root, so it is excluded, never signalled, and the stop is confirmed.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 500, denies_access=True),
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed == []
        assert api.handleless_queries == [200]
        assert 200 not in api.terminate_order
        assert api.table[200].alive
        assert api.terminate_order == [ROOT]

    def test_b_stale_parent_reused_pid_openable_is_excluded(self):
        # (b) same old stranger, openable: its own creation time predates the
        # root, so it is excluded without the handle-less lookup and the stop
        # is confirmed while the genuine child is still terminated.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 500),
            FakeProc(300, ROOT, 2_000),             # genuine child
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed == []
        assert api.table[200].alive and 200 not in api.terminate_order
        assert api.terminate_order == [300, ROOT]

    def test_c_later_child_query_denied_stays_unconfirmed(self):
        # (c) a real child (created after the root) that denies the query open:
        # the handle-less creation is NOT older than the root, so it stays
        # unconfirmed - handle-less information never authorizes termination -
        # and the record carries image name and both creation times.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000, denies_access=True, name="child.exe"),
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert len(unconfirmed) == 1
        assert "pid 200 (child.exe)" in unconfirmed[0]
        assert "_ProcessDenied" in unconfirmed[0]
        assert "created 2000" in unconfirmed[0]
        assert "nominal parent 100 created 1000" in unconfirmed[0]
        assert 200 not in api.terminate_order and api.table[200].alive
        assert api.terminate_order == [ROOT]

    def test_d_later_child_terminate_denied_stays_unconfirmed(self):
        # (d) a real child, openable, but termination is refused: membership
        # was verified, yet an unterminatable member keeps the stop unconfirmed.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000),
        ])
        api.terminate_fail_pids = {200}
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("pid 200 (stub.exe) created 2000" in u and "terminate refused" in u
                   for u in unconfirmed)
        assert api.table[200].alive
        assert api.terminate_order == [200, ROOT]

    def test_handleless_query_failure_keeps_stranger_unconfirmed(self):
        # Fail-closed: when even the handle-less table cannot answer, the stop
        # stays unconfirmed; exclusion requires proof of age, not absence.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 500, denies_access=True),
        ])
        api.fails_handleless = True
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert len(unconfirmed) == 1 and "200" in unconfirmed[0]
        assert "creation unknown" in unconfirmed[0]
        assert api.terminate_order == [ROOT]

    def test_equal_creation_times_keep_current_unconfirmed_handling(self):
        # A candidate created at exactly the parent's creation time keeps the
        # historical handling: never signalled, reported with both times.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION),
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert len(unconfirmed) == 1
        assert "creation 1000 does not bind to parent 100 created 1000" in unconfirmed[0]
        assert 200 not in api.terminate_order and api.table[200].alive
        assert api.terminate_order == [ROOT]

    def test_permission_split_query_ok_terminate_denied_stays_unconfirmed(self):
        # Two-step open: membership verified with the query handle, but the
        # terminate open is denied: unconfirmed, never signalled.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000, denies_terminate_access=True),
        ])
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("termination handle unavailable (_ProcessDenied)" in u and "pid 200" in u
                   for u in unconfirmed)
        assert 200 not in api.terminate_order and api.table[200].alive
        assert api.terminate_order == [ROOT]

    def test_both_query_and_terminate_handles_are_released(self):
        # Dual-handle bookkeeping: every signalled member holds one query and
        # one terminate handle during the stop, and both are closed.
        api = api_with_stub_chain()
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert unconfirmed == []
        for pid in (ROOT, 200, 300):
            assert api.table[pid].query_handle in api.closed_handles
            assert api.table[pid].terminate_handle in api.closed_handles
        assert len(api.closed_handles) == 6
        # A denied query open releases nothing it never took.
        api2 = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000, denies_access=True),
        ])
        tree(ROOT, ROOT_CREATION, api2)
        assert set(api2.closed_handles) == {api2.table[ROOT].query_handle,
                                            api2.table[ROOT].terminate_handle}


# ------------------------ CL-25 fix round (codex spec A + revised-B)


def stop_record_with_api(monkeypatch, tmp_path, api):
    """Run stop_owned('worker') against a fake tree api; return its result.

    The record names the fake root (ROOT, win:ROOT_CREATION) and birth_identity
    reports the root alive exactly until the fake tree kills it, so the shared
    record is forgotten only when the tree stop confirms every member.
    """
    import json
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    record = {"version": 1, "name": "worker", "pid": ROOT,
              "birth": f"win:{ROOT_CREATION}", "port": 8611, "state": "ready"}
    pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
    pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")
    real_tree = pc._win_terminate_tree
    record_api = api

    def _tree(pid, creation, api=None, **kw):
        # Forward an explicitly passed api so later direct tree() calls inside
        # the same test still reach their own fake despite this monkeypatch.
        return real_tree(pid, creation, api=api or record_api,
                         deadline_s=kw.get("deadline_s", 10.0))
    monkeypatch.setattr(pc, "_win_terminate_tree", _tree)
    monkeypatch.setattr(pc, "birth_identity",
                        lambda pid: f"win:{ROOT_CREATION}" if api.table[ROOT].alive else None)
    return pc.stop_owned("worker", timeout=5)


# stop_owned takes the POSIX pidfd branch on non-Windows, so these fake-API
# scripts of the Windows stop path (and their record lifecycle) never engage
# there; skip them rather than assert a code path that was never driven.
@pytest.mark.skipif(os.name != "nt", reason="Windows-only stop-tree semantics via fake API")
class TestTerminateFailureRecheck:
    """Codex spec A: a refused TerminateProcess - or a terminate-handle open
    failure - is neither evidence that the member lives nor that it exited.
    Only the SAME still-held query handle entering the signaled state confirms
    the member's exit; timeouts, wait failures and errors stay unconfirmed."""

    def test_self_exit_before_terminate_request_confirms_member_and_deletes_record(self, tmp_path, monkeypatch):
        # Race 1: the stub exits on its own between the exited()==False check
        # and the terminate request, so TerminateProcess returns FALSE. The
        # pinned query handle is signaled: the stop is confirmed and the
        # shared record is deleted instead of being left for manual review.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),          # launcher stub (root)
            FakeProc(200, ROOT, 2_000),                # its interpreter
        ])
        api.terminate_fail_pids = {ROOT}
        real_terminate = api.terminate

        def self_exit_then_refuse(handle):
            if api._by_handle(handle).pid == ROOT:
                api.table[ROOT].alive = False          # exits before the signal lands
            return real_terminate(handle)
        api.terminate = self_exit_then_refuse
        try:
            result = stop_record_with_api(monkeypatch, tmp_path, api)
        except RuntimeError as exc:
            pytest.fail(f"stop not confirmed: {exc}")
        assert result is True
        assert not pc.record_path("worker").exists()

    def test_self_exit_before_terminate_handle_open_confirms_member_and_deletes_record(self, tmp_path, monkeypatch):
        # Same race, earlier point: the stub exits before the terminate handle
        # is even opened; the still-held query handle still confirms the exit.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000),
        ])
        real_open_terminate = api.open_terminate

        def self_exit_then_no_handle(pid):
            if pid == ROOT:
                api.table[ROOT].alive = False
                raise pc._ProcessGone(pid)
            return real_open_terminate(pid)
        api.open_terminate = self_exit_then_no_handle
        try:
            result = stop_record_with_api(monkeypatch, tmp_path, api)
        except RuntimeError as exc:
            pytest.fail(f"stop not confirmed: {exc}")
        assert result is True
        assert not pc.record_path("worker").exists()
        assert api.terminate_order == [200]            # no signal attempted for the root

    def test_terminate_refusal_with_living_member_keeps_unconfirmed_and_record(self, tmp_path, monkeypatch):
        # FALSE and the member is still alive on the pinned handle: the
        # re-verification does not confirm, so the record is retained.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000),
        ])
        api.terminate_fail_pids = {200}
        with pytest.raises(RuntimeError, match="stop not confirmed.*terminate refused"):
            stop_record_with_api(monkeypatch, tmp_path, api)
        assert pc.record_path("worker").exists()

    def test_terminate_refusal_with_failing_recheck_keeps_unconfirmed_and_record(self, tmp_path, monkeypatch):
        # FALSE and the re-verification itself errors (WAIT_FAILED analog):
        # an error is not an exit.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, 2_000),
        ])
        api.terminate_fail_pids = {200}
        real_wait = api.wait

        def wait(handle, ms):
            if api._by_handle(handle).pid == 200:
                raise OSError("wait failed")
            return real_wait(handle, ms)
        api.wait = wait
        with pytest.raises(RuntimeError, match="stop not confirmed.*terminate refused"):
            stop_record_with_api(monkeypatch, tmp_path, api)
        assert pc.record_path("worker").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows-only stop-tree semantics via fake API")
class TestEqualTickHandleAnchoredRecheck:
    """Codex revised-B: an equal-creation-tick entry is never terminated. Only
    the SAME continuously-held query handle entering the signaled state after
    the stop resolves that one member's exit doubt - in any final-table state,
    without terminate permission ever being requested, and without proving
    anything about its descendants. Final-table absence alone never releases an
    unconfirmed member, and a confirmed exit is never membership confirmation:
    candidate descendants of the equal-tick member are retained from the first
    snapshot, and any surviving, unknown or failing one keeps the record."""

    def _conhost_like(self, drop_from_final):
        """Stub root plus a same-tick conhost.exe that self-exits mid-stop."""
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION, name="conhost.exe"),
        ])
        real_snapshot, real_terminate = api.snapshot, api.terminate

        def snapshot():
            parents, ft = real_snapshot()
            if drop_from_final and api.snapshots == 3:
                parents = {pid: value for pid, value in parents.items() if pid != 200}
            return parents, ft

        def terminate(handle):
            api.table[200].alive = False              # self-exits during the stop
            return real_terminate(handle)
        api.snapshot, api.terminate = snapshot, terminate
        return api

    def _equal_tick_alive_missing_from_final(self):
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION, name="stranger.exe"),
        ])
        real_snapshot = api.snapshot

        def snapshot():
            parents, ft = real_snapshot()
            if api.snapshots == 3:
                parents = {pid: value for pid, value in parents.items() if pid != 200}
            return parents, ft
        api.snapshot = snapshot
        return api

    def test_equal_tick_self_exit_confirms_with_and_without_final_table_entry(self):
        # Race 2: the retained original handle is signaled, so the member is
        # confirmed whether the final table still lists the pinned corpse or
        # has already forgotten the pid - and terminate permission for it is
        # never requested at all.
        for drop_from_final in (False, True):
            api = self._conhost_like(drop_from_final)
            assert tree(ROOT, ROOT_CREATION, api) == []
            assert api.terminate_order == [ROOT]
            assert api.terminate_opens == [ROOT]

    def test_equal_tick_table_absence_never_releases_unconfirmed(self, tmp_path, monkeypatch):
        # Alive, unopenable, or a failing pinned-handle query: table absence
        # must not succeed. A handle-confirmed exit still needs the final
        # table check, which failing also keeps the stop unconfirmed.
        # (a) alive at recheck and dropped from the final table.
        api = self._equal_tick_alive_missing_from_final()
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("pid 200" in u and "does not bind" in u for u in unconfirmed)
        assert 200 not in api.terminate_order and api.table[200].alive
        with pytest.raises(RuntimeError, match="stop not confirmed"):
            stop_record_with_api(monkeypatch, tmp_path, self._equal_tick_alive_missing_from_final())
        assert pc.record_path("worker").exists()
        # (b) no handle: the query open is denied and the handle-less table
        # reports an equal creation; no pinned handle can ever confirm it.
        api_b = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION, denies_access=True),
        ])
        real_snapshot_b = api_b.snapshot

        def snapshot_b():
            parents, ft = real_snapshot_b()
            if api_b.snapshots == 3:
                parents = {pid: value for pid, value in parents.items() if pid != 200}
            return parents, ft
        api_b.snapshot = snapshot_b
        unconfirmed = tree(ROOT, ROOT_CREATION, api_b)
        assert any("pid 200" in u and "identity unavailable" in u for u in unconfirmed)
        assert api_b.handleless_queries == [200]
        assert 200 not in api_b.terminate_order and api_b.table[200].alive
        # (c) query fails: the pinned-handle exit wait itself errors at recheck.
        api_c = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION),
        ])
        real_wait_c = api_c.wait

        def wait_c(handle, ms):
            if api_c._by_handle(handle).pid == 200:
                raise OSError("wait failed")
            return real_wait_c(handle, ms)
        api_c.wait = wait_c
        unconfirmed = tree(ROOT, ROOT_CREATION, api_c)
        assert any("pid 200" in u and "does not bind" in u for u in unconfirmed)
        assert 200 not in api_c.terminate_order and api_c.table[200].alive
        # (d) the exit IS handle-confirmed, but the final table check fails:
        # the stop is still not confirmed.
        api_d = self._conhost_like(drop_from_final=False)
        real_snapshot_d = api_d.snapshot

        def fail_final_snapshot():
            result = real_snapshot_d()          # let the counter advance first
            if api_d.snapshots == 3:
                raise OSError("final snapshot denied")
            return result
        api_d.snapshot = fail_final_snapshot
        unconfirmed = tree(ROOT, ROOT_CREATION, api_d)
        assert unconfirmed == ["final tree check unavailable; record retained"]

    def test_equal_tick_descendant_survival_fails_the_stop_and_keeps_record(self, tmp_path, monkeypatch):
        # The equal-tick member itself exits (its own doubt is resolved), but
        # a surviving, newly appearing, or unprovable descendant keeps the
        # stop failed and the record retained; nothing below an equal-tick
        # edge is ever signalled.
        # (i) existing descendant, alive since the first snapshot.
        api = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION),        # equal-tick member
            FakeProc(300, 200, 3_000),                 # its live descendant
        ])
        real_terminate = api.terminate

        def member_self_exit(handle):
            api.table[200].alive = False
            return real_terminate(handle)
        api.terminate = member_self_exit
        unconfirmed = tree(ROOT, ROOT_CREATION, api)
        assert any("pid 300" in u for u in unconfirmed)
        assert not any(u.startswith("pid 200 ") for u in unconfirmed)  # doubt resolved
        assert 200 not in api.terminate_order and 300 not in api.terminate_order
        assert api.table[300].alive

        def fresh_case_i():
            fresh = FakeWinApi([
                FakeProc(ROOT, 1, ROOT_CREATION),
                FakeProc(200, ROOT, ROOT_CREATION),
                FakeProc(300, 200, 3_000),
            ])
            fresh_terminate = fresh.terminate

            def self_exit(handle):
                fresh.table[200].alive = False
                return fresh_terminate(handle)
            fresh.terminate = self_exit
            return fresh
        with pytest.raises(RuntimeError, match="stop not confirmed"):
            stop_record_with_api(monkeypatch, tmp_path, fresh_case_i())
        assert pc.record_path("worker").exists()
        # (ii) new descendant: born after the first snapshot, listed in the
        # final table under the (already exited) equal-tick member.
        api_ii = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION),
        ])
        real_snapshot_ii, real_terminate_ii = api_ii.snapshot, api_ii.terminate

        def late_child_in_final():
            parents, ft = real_snapshot_ii()
            if api_ii.snapshots == 3:
                parents[400] = (200, "late.exe")
                api_ii.table[400] = FakeProc(400, 200, 9_000, name="late.exe")
            return parents, ft

        def member_self_exit_ii(handle):
            api_ii.table[200].alive = False
            return real_terminate_ii(handle)
        api_ii.snapshot, api_ii.terminate = late_child_in_final, member_self_exit_ii
        unconfirmed = tree(ROOT, ROOT_CREATION, api_ii)
        assert any("pid 400" in u and "residual or new descendant" in u for u in unconfirmed)
        assert 400 not in api_ii.terminate_order and api_ii.table[400].alive
        # (iii) observed-then-vanished intermediate: the member's child was in
        # the first snapshot but exits and is reaped before a handle is taken,
        # and its own child survives.
        api_iii = FakeWinApi([
            FakeProc(ROOT, 1, ROOT_CREATION),
            FakeProc(200, ROOT, ROOT_CREATION),        # equal-tick member
            FakeProc(300, 200, 3_000),                 # observed intermediate
            FakeProc(400, 300, 4_000),                 # intermediate's live child
        ])
        real_snapshot_iii, real_terminate_iii = api_iii.snapshot, api_iii.terminate

        def vanish_after_first_snapshot():
            parents, ft = real_snapshot_iii()
            if api_iii.snapshots == 1:
                api_iii.table[300].reaped = True       # gone before the subtree walk
            return parents, ft

        def member_self_exit_iii(handle):
            api_iii.table[200].alive = False
            return real_terminate_iii(handle)
        api_iii.snapshot, api_iii.terminate = vanish_after_first_snapshot, member_self_exit_iii
        unconfirmed = tree(ROOT, ROOT_CREATION, api_iii)
        assert any("pid 300" in u for u in unconfirmed)     # exit unknown: no handle
        assert any("pid 400" in u for u in unconfirmed)     # still alive
        assert 300 not in api_iii.terminate_order and 400 not in api_iii.terminate_order
        assert api_iii.table[400].alive


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


# ------------------- anchored death checks (CL-25 signature-3 class)


def _valid_win_birth(birth):
    return (isinstance(birth, str) and birth.startswith("win:")
            and birth[4:].isascii() and birth[4:].isdecimal()
            and int(birth[4:]) > 0)


def await_original_identity_gone(query, anchor, deadline, *, now=time.monotonic,
                                 sleep=time.sleep):
    """Identity-anchored death verdict for one tree member (CL-25 signature-3
    class, ported from launcher_lifecycle eda84e6).

    The anchor comes from a creation handle or a writer-alive handshake,
    not a postmortem PID lookup. None OR a different valid Windows identity
    confirms that original is gone. Same, unknown and query errors do not.
    The loop shares the caller's single deadline; one sample after the
    deadline decides the verdict. Returns (gone, last), including a deciding
    OSError rather than treating that error as successful cleanup.
    """
    if not _valid_win_birth(anchor):
        raise ValueError("original Windows identity is required")

    def confirmed(sample):
        return sample is None or (_valid_win_birth(sample) and sample != anchor)

    gone = False
    last = None
    while now() < deadline:
        try:
            last = query()
        except OSError as exc:
            last = exc  # a query failure is not an exit; keep the window
        else:
            if confirmed(last):
                gone = True
                break
        sleep(0.1)
    if not gone:
        try:
            last = query()
        except OSError as exc:
            last = exc
        gone = confirmed(last)
    return gone, last


class TestAnchoredDeathCheck:
    """Deterministic regressions for the identity-anchored death check ported
    from launcher_lifecycle's CL-25 signature-3 fix (eda84e6): the real-Windows
    tests poll member pids whose ORIGINAL identities were anchored at first
    sight, so a pid later held by a churn reuser reads as a DIFFERENT identity
    and still confirms the original is gone. Fake samplers and a fake clock
    exercise every verdict rule with no real pid churn."""

    @staticmethod
    def _clock(start=100):
        # Integer ticks: no 0.1 accumulation can keep the deciding query in
        # the loop just below the deadline (100.99999999999994 < 101).
        state = {"t": start}

        def sleep(seconds):
            assert seconds == 0.1
            state["t"] += 1
        return lambda: state["t"], sleep

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
            self._sticky(["win:1000"]), "win:1000", 105.0, now=now, sleep=sleep)
        assert gone is False
        assert last == "win:1000"

    def test_pid_reuse_different_identity_passes(self):
        # Signature-3 class: the pid is later held by a DIFFERENT process; a
        # different valid identity confirms the original identity is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:1000", "win:1000", "win:2000"]),
            "win:1000", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last == "win:2000"

    def test_reuse_before_first_sample_passes(self):
        # The pid got reused BEFORE the first poll: the first query already
        # reports the reuser while the anchor keeps the first-sight identity.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:2000"]), "win:1000", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last == "win:2000"

    def test_none_sample_passes(self):
        # Nothing holds the pid any more: the original identity is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:1000", None]), "win:1000", 105.0,
            now=now, sleep=sleep)
        assert gone is True
        assert last is None

    def test_identity_query_error_is_not_an_exit(self):
        # Query exceptions are neither an exit nor a failure; the verdict only
        # lands on a later None/different-identity sample.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed"), OSError("probe failed"), None]),
            "win:1000", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last is None

    def test_identity_query_error_at_deadline_does_not_pass(self):
        # Only exceptions until the deadline: the original was never confirmed
        # gone, so the verdict must not pass.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed")]), "win:1000", 105.0,
            now=now, sleep=sleep)
        assert gone is False
        assert isinstance(last, OSError)

    @pytest.mark.parametrize("final, expected", [
        pytest.param(None, True, id="absent"),
        pytest.param("win:2000", True, id="reused"),
        pytest.param("win:1000", False, id="same"),
        pytest.param(OSError("probe failed"), False, id="error"),
        pytest.param("unknown", False, id="unknown"),
        pytest.param("win:0", False, id="zero"),
        pytest.param("win:not-a-time", False, id="malformed"),
    ])
    def test_deciding_sample_is_after_deadline(self, final, expected):
        now, sleep = self._clock()
        deadline = 103
        samples = []

        def query():
            query_time = now()
            value = "win:1000" if query_time < deadline else final
            samples.append((query_time, value))
            if isinstance(value, OSError):
                raise value
            return value

        gone, last = await_original_identity_gone(
            query, "win:1000", deadline, now=now, sleep=sleep)
        # These are behavior assertions: removing the final query must fail
        # even for a final same/error/unknown result, not just the exit cases.
        assert samples == [(100, "win:1000"), (101, "win:1000"),
                           (102, "win:1000"), (103, final)]
        assert samples[-1][0] >= deadline
        assert gone is expected
        assert last is final

    @pytest.mark.parametrize("anchor", [None, "unknown", "win:0", "win:no"])
    def test_unknown_original_anchor_is_rejected(self, anchor):
        queried = []
        with pytest.raises(ValueError, match="original Windows identity"):
            await_original_identity_gone(lambda: queried.append(True), anchor, 103)
        assert queried == []

    @pytest.mark.parametrize("unknown", ["unknown", "", "win:0", "win:no", False])
    def test_unknown_loop_sample_does_not_confirm_exit(self, unknown):
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([unknown]), "win:1000", 103, now=now, sleep=sleep)
        assert gone is False
        assert last is unknown


class _FakeOwnedRuntime:
    """Drive the actual real-test setup/verdict/teardown without OS processes.

    Handles keep referencing the original object even after table PID reuse.
    Closing the writer's input models its cooperative, creation-handle cleanup;
    the writer itself is also exercised separately below.
    """

    def __init__(self, monkeypatch, tmp_path, *, chain=False):
        import io
        self.originals = [FakeProc(ROOT, 1, ROOT_CREATION),
                          FakeProc(200, ROOT, 2_000)]
        if chain:
            self.originals.append(FakeProc(300, 200, 3_000))
        self.bystander = FakeProc(999, 1, 900)
        self.api = FakeWinApi([*self.originals, self.bystander])
        self.created = []
        self.writer = self.originals[1] if chain else self.originals[0]
        self.on_parent_kill = lambda: None
        self.birth_override = {}
        handles = {h: p for p in [*self.originals, self.bystander]
                   for h in (p.query_handle, p.terminate_handle)}
        by_handle = self.api._by_handle
        self.api._by_handle = lambda h: handles[h] if h in handles else by_handle(h)
        runtime = self

        class CreationHandle:
            def __init__(self, member):
                self.closed = False
                handles[self] = member

            def Close(self):
                self.closed = True

        class Process:
            def __init__(self, command, **kw):
                is_bystander = command[-1] == "import time;time.sleep(60)"
                self.member = runtime.bystander if is_bystander else runtime.originals[0]
                self.pid = self.member.pid
                self._handle = CreationHandle(self.member)
                self.stdin = io.StringIO() if kw.get("stdin") == subprocess.PIPE else None
                self.stdout = io.StringIO("200\n") if kw.get("stdout") == subprocess.PIPE else None
                self.stderr = io.StringIO() if kw.get("stderr") == subprocess.PIPE else None
                runtime.created.append(self)
                if not is_bystander:
                    for name, member in (("real", runtime.originals[1]),
                                         ("child", runtime.originals[1]),
                                         ("leaf", runtime.originals[-1])):
                        path = tmp_path / (name + ".pid")
                        path.with_suffix(".birth").write_text(f"win:{member.creation}")
                        path.write_text(str(member.pid))

            def poll(self):
                return None if self.member.alive else 0

            def kill(self):
                runtime.api.terminate(self.member.terminate_handle)
                self.member.alive = False
                if self.pid == ROOT:
                    runtime.on_parent_kill()

            def wait(self, timeout):
                if (self.member.alive and runtime.writer.alive
                        and self.stdin is not None and self.stdin.closed):
                    # The writer owns its child from Popen; EOF asks it to
                    # release that original, never whoever now holds the pid.
                    for member in runtime.originals[1:]:
                        if member.alive:
                            runtime.api.terminate(member.terminate_handle)
                            member.alive = False
                    self.member.alive = False
                if self.member.alive:
                    raise subprocess.TimeoutExpired("fake owned process", timeout)
                return 0

        def birth(pid):
            if pid in self.birth_override:
                value = self.birth_override[pid]
                if isinstance(value, Exception):
                    raise value
                return value
            member = self.api.table.get(pid)
            return f"win:{member.creation}" if member and member.alive else None

        monkeypatch.setattr(subprocess, "Popen", Process)
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0, raising=False)
        monkeypatch.setattr(pc, "_RealWinApi", lambda: self.api)
        monkeypatch.setattr(pc, "birth_identity", birth)
        # Never let the fake safety harness enter POSIX pidfd signalling.
        def dead_stop(*args, **kw):
            raise RuntimeError("dead root; record retained")
        monkeypatch.setattr(pc, "stop_owned", dead_stop)

    def reuse_child(self):
        self.originals[1].alive = False
        replacement = FakeProc(200, 555, 9_000)
        self.api.table[200] = replacement
        return replacement

    def assert_released(self):
        assert all(not p.alive for p in self.originals)
        assert all(not proc.member.alive and proc._handle.closed for proc in self.created)
        assert all(stream is None or stream.closed for proc in self.created
                   for stream in (proc.stdin, proc.stdout, proc.stderr))
        assert len(self.api.closed_handles) == len(set(self.api.closed_handles))


class TestRealProcessSafetyFakes:
    """Run the real tests' control flow, but every process/handle is fake."""

    @pytest.mark.parametrize("case", ["handleless", "dead_root"])
    def test_postmortem_pid_reuser_receives_zero_termination(self, tmp_path, monkeypatch, case):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        if case == "handleless":
            def creation_without_handle(pid):
                value = runtime.api.table[pid].creation
                if pid == 200:
                    runtime.reuse_child()
                return value
            monkeypatch.setattr(runtime.api, "creation_without_handle", creation_without_handle)
            TestRealWindowsProcesses().test_handleless_creation_matches_handle_query_on_real_processes(tmp_path)
        else:
            def stop(*args, **kw):
                runtime.reuse_child()
                raise RuntimeError("dead root; record retained")
            monkeypatch.setattr(pc, "stop_owned", stop)
            TestRealWindowsProcesses().test_dead_root_candidates_surface_and_record_is_kept(tmp_path, monkeypatch)
        assert runtime.api.table[200].alive
        assert 200 not in runtime.api.terminate_order
        assert 200 not in runtime.api.terminate_opens

    def test_liveness_rejects_a_pid_reuser(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        runtime.on_parent_kill = runtime.reuse_child
        with pytest.raises(AssertionError):
            TestRealWindowsProcesses().test_dead_root_candidates_surface_and_record_is_kept(tmp_path, monkeypatch)
        assert runtime.api.table[200].alive
        assert 200 not in runtime.api.terminate_order

    @pytest.mark.parametrize("stage", ["real_pid", "leaf_pid", "identity", "record"])
    def test_partial_setup_releases_every_owned_process(self, tmp_path, monkeypatch, stage):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path, chain=True)
        if stage.endswith("_pid"):
            read_pid = _read_pid
            def fail_read(path, timeout=10.0):
                if Path(path).stem == stage[:-4]:
                    raise OSError("injected setup failure")
                return read_pid(path, timeout)
            monkeypatch.setitem(globals(), "_read_pid", fail_read)
        elif stage == "identity":
            runtime.birth_override[200] = OSError("injected setup failure")
            creation = runtime.api.creation
            def fail_creation(handle):
                if runtime.api._by_handle(handle).pid == 200:
                    raise OSError("injected setup failure")
                return creation(handle)
            monkeypatch.setattr(runtime.api, "creation", fail_creation)
        else:
            write = Path.write_text
            def fail_record(path, *args, **kw):
                if path.name == "worker.pid":
                    raise OSError("injected setup failure")
                return write(path, *args, **kw)
            monkeypatch.setattr(Path, "write_text", fail_record)
        with pytest.raises(OSError, match="injected setup failure"):
            if stage == "record":
                TestRealWindowsProcesses().test_stop_owned_kills_the_whole_real_tree(tmp_path, monkeypatch)
            else:
                TestRealWindowsProcesses().test_unrelated_process_survives_a_tree_stop(tmp_path, monkeypatch)
        runtime.assert_released()
        if stage != "record":
            assert not runtime.bystander.alive

    @pytest.mark.parametrize("value", ["unknown", "win:0", OSError("identity denied")])
    def test_unknown_cleanup_identity_fails_closed_and_keeps_record(self, tmp_path, monkeypatch, value):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        def stop(*args, **kw):
            runtime.birth_override[200] = value
            raise RuntimeError("dead root; record retained")
        monkeypatch.setattr(pc, "stop_owned", stop)
        with pytest.raises((RuntimeError, OSError), match="identity"):
            TestRealWindowsProcesses().test_dead_root_candidates_surface_and_record_is_kept(tmp_path, monkeypatch)
        assert runtime.originals[1].alive
        assert 200 not in runtime.api.terminate_order
        assert 200 not in runtime.api.terminate_opens
        assert runtime.originals[1].query_handle in runtime.api.closed_handles
        assert runtime.created[0]._handle.closed
        assert pc.record_path("worker").exists()

    def test_writer_cleanup_failure_is_not_silently_successful(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        with pytest.raises(RuntimeError, match="writer cleanup failed"):
            with _OwnedWindowsProcesses() as owned:
                parent, *_ = TestRealWindowsProcesses()._spawn_pair(tmp_path, owned)
                wait = parent.wait
                def failed_writer(timeout):
                    wait(timeout)
                    return 1
                monkeypatch.setattr(parent, "wait", failed_writer)
        runtime.assert_released()

    @pytest.mark.parametrize("stage", ["spawn_identity", "child_pid", "birth_read"])
    def test_partial_setup_before_pin_keeps_writer_ownership(self, tmp_path, monkeypatch, stage):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        if stage == "spawn_identity":
            monkeypatch.setattr(runtime.api, "creation",
                                lambda handle: (_ for _ in ()).throw(OSError("injected setup failure")))
        elif stage == "child_pid":
            monkeypatch.setitem(globals(), "_read_pid",
                                lambda *args: (_ for _ in ()).throw(OSError("injected setup failure")))
        else:
            read = Path.read_text
            def fail_read(path, *args, **kw):
                if path.suffix == ".birth":
                    raise OSError("injected setup failure")
                return read(path, *args, **kw)
            monkeypatch.setattr(Path, "read_text", fail_read)
        with pytest.raises(OSError, match="injected setup failure"):
            TestRealWindowsProcesses().test_handleless_creation_matches_handle_query_on_real_processes(tmp_path)
        runtime.assert_released()

    def test_cleanup_failure_does_not_skip_other_members_or_handles(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path, chain=True)
        with pytest.raises(RuntimeError, match="cleanup exit unconfirmed"):
            with _OwnedWindowsProcesses() as owned:
                owned.spawn([sys._base_executable, "-c", "import time;time.sleep(60)"])
                # Kill only the parent, leaving both pinned descendants for
                # independent rescue, including one refused termination.
                parent, *_ = TestRealWindowsProcesses()._spawn_chain(tmp_path, owned)
                parent.kill()
                runtime.api.terminate_fail_pids = {300}
                raise AssertionError("injected verdict failure")
        assert runtime.originals[2].alive  # refusal must NOT turn into success
        assert not runtime.originals[1].alive
        assert not runtime.bystander.alive
        assert set(runtime.api.closed_handles) == {
            p.query_handle for p in runtime.originals[1:]
        } | {p.terminate_handle for p in runtime.originals[1:]}
        assert all(proc._handle.closed for proc in runtime.created)

    def test_cleanup_query_error_does_not_skip_other_owned_members(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path, chain=True)
        with pytest.raises(OSError, match="identity denied"):
            with _OwnedWindowsProcesses() as owned:
                owned.spawn([sys._base_executable, "-c", "import time;time.sleep(60)"])
                parent, *_ = TestRealWindowsProcesses()._spawn_chain(tmp_path, owned)
                parent.kill()
                runtime.birth_override[300] = OSError("identity denied")
        assert runtime.originals[2].alive
        assert 300 not in runtime.api.terminate_order
        assert 300 not in runtime.api.terminate_opens
        assert not runtime.originals[1].alive
        assert not runtime.bystander.alive
        assert all(p.query_handle in runtime.api.closed_handles for p in runtime.originals[1:])
        assert all(proc._handle.closed for proc in runtime.created)

    @pytest.mark.parametrize("current", [None, "win:9000"])
    def test_current_absent_or_different_only_suppresses_cleanup(self, tmp_path, monkeypatch, current):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        with _OwnedWindowsProcesses() as owned:
            parent, *_ = TestRealWindowsProcesses()._spawn_pair(tmp_path, owned)
            parent.kill()
            runtime.birth_override[200] = current
        assert runtime.api.terminate_order == [ROOT]
        assert runtime.api.terminate_opens == []
        assert runtime.originals[1].query_handle in runtime.api.closed_handles

    def test_rescue_cannot_turn_a_broken_stop_into_success(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path, chain=True)
        def cascade_on_rescue():
            for member in runtime.originals[1:]:
                member.alive = False
        runtime.on_parent_kill = cascade_on_rescue
        def broken_stop(*args, **kw):
            pc.record_path("worker").unlink()
            return True  # lies about stopping; all members are still alive
        monkeypatch.setattr(pc, "stop_owned", broken_stop)
        now, sleep = TestAnchoredDeathCheck._clock()
        check = await_original_identity_gone
        monkeypatch.setattr(time, "monotonic", now)
        monkeypatch.setitem(globals(), "await_original_identity_gone",
                            lambda query, birth, deadline: check(
                                query, birth, deadline, now=now, sleep=sleep))
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            TestRealWindowsProcesses().test_stop_owned_kills_the_whole_real_tree(tmp_path, monkeypatch)
        runtime.assert_released()


class TestOwnedWriterFakes:
    def test_descendant_does_not_inherit_cleanup_input(self, tmp_path, monkeypatch):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        monkeypatch.setattr(os, "getpid", lambda: 200)
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        popen = subprocess.Popen
        inherited = []

        def spawn(command, **kw):
            # A sleeper must not inherit the writer's cleanup pipe. On
            # Windows its stdin initialization can block behind the writer's
            # pending EOF read, before it ever publishes its identity.
            inherited.append(kw.get("stdin") != subprocess.DEVNULL)
            return popen(command, **kw)
        monkeypatch.setattr(subprocess, "Popen", spawn)
        _owned_writer(tmp_path / "writer.pid",
                      [sys._base_executable, "-c", "import time;time.sleep(60)"])
        assert inherited == [False]
        assert not runtime.bystander.alive

    @pytest.mark.parametrize("stage", ["identity", "birth", "pid", "wait", "success"])
    def test_writer_releases_spawned_child_on_every_setup_exit(self, tmp_path, monkeypatch, stage):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        monkeypatch.setattr(os, "getpid", lambda: 200)
        if stage == "identity":
            def fail_creation(handle):
                raise OSError("writer setup failure")
            monkeypatch.setattr(runtime.api, "creation", fail_creation)
        elif stage in {"birth", "pid"}:
            write = Path.write_text
            def fail_write(path, *args, **kw):
                if path == (tmp_path / ("writer.birth" if stage == "birth" else "writer.ready")):
                    raise OSError("writer setup failure")
                return write(path, *args, **kw)
            monkeypatch.setattr(Path, "write_text", fail_write)
        else:
            def sleep(seconds):
                assert seconds == 60
                if stage == "wait":
                    raise OSError("writer setup failure")
            monkeypatch.setattr(time, "sleep", sleep)
        command = [sys._base_executable, "-c", "import time;time.sleep(60)"]
        if stage == "success":
            _owned_writer(tmp_path / "writer.pid", command)
            assert (tmp_path / "writer.pid").read_text() == "200"
            assert (tmp_path / "writer.birth").read_text() == "win:2000"
        else:
            with pytest.raises(OSError, match="writer setup failure"):
                _owned_writer(tmp_path / "writer.pid", command)
        assert not runtime.bystander.alive
        assert runtime.api.terminate_order == [999]
        assert runtime.created[0]._handle.closed
        assert runtime.originals[1].query_handle in runtime.api.closed_handles

    @pytest.mark.parametrize("failure", ["kill", "wait"])
    def test_creation_handle_cleanup_failure_still_releases_resources(self, tmp_path, monkeypatch, failure):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        proc = subprocess.Popen([sys._base_executable, "-c", "import time;time.sleep(60)"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def fail(*args, **kw):
            raise OSError("injected cleanup failure")
        monkeypatch.setattr(proc, failure, fail)
        with pytest.raises(OSError, match="injected cleanup failure"):
            _cleanup_spawned(proc)
        assert proc._handle.closed and proc.stdout.closed and proc.stderr.closed
        assert runtime.api.terminate_opens == []  # no PID-based rescue

    @pytest.mark.parametrize("failure", ["unknown", "reused", "dead", "query_error"])
    def test_writer_handshake_failure_closes_handles_without_signalling(self, tmp_path, monkeypatch, failure):
        runtime = _FakeOwnedRuntime(monkeypatch, tmp_path)
        (tmp_path / "child.pid").write_text("200")
        (tmp_path / "child.birth").write_text("unknown" if failure == "unknown" else "win:2000")
        if failure == "reused":
            runtime.reuse_child()
        elif failure == "dead":
            runtime.originals[1].alive = False
        elif failure == "query_error":
            def fail_creation(handle):
                raise OSError("identity unavailable")
            monkeypatch.setattr(runtime.api, "creation", fail_creation)
        with pytest.raises(OSError):
            with _OwnedWindowsProcesses() as owned:
                owned.pin_writer(tmp_path / "child.pid")
        assert runtime.api.terminate_order == []
        assert runtime.api.terminate_opens == []
        if failure != "unknown":
            assert runtime.api.table[200].query_handle in runtime.api.closed_handles
        if failure == "reused":
            assert runtime.api.table[200].alive

# ---------------------------------------------------------- real processes


def _read_pid(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not Path(path).exists():
        time.sleep(0.05)
    return int(Path(path).read_text())


def _cleanup_spawned(proc):
    """Only Popen's original creation handle authorizes this rescue.

    A cooperative writer gets EOF so it can reclaim children even if their
    PID/identity handshakes were never read. Ordinary sleepers are killed via
    their creation handle. Release every stream/handle even if a wait fails.
    """
    with ExitStack() as release:
        release.callback(proc._handle.Close)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                release.callback(stream.close)
        cooperative = getattr(proc, "_bzs_cooperative", False)
        requested_cleanup = proc.poll() is None and cooperative
        if proc.poll() is None:
            if cooperative:
                proc.stdin.close()
            else:
                proc.kill()
        result = proc.wait(timeout=5)
        if requested_cleanup and result != 0:
            raise RuntimeError(f"pid {proc.pid}: writer cleanup failed ({result})")


def _owned_writer(pid_path, child_command=None, cooperative=False):
    """Child-side ownership starts at Popen, before any identity/file work.

    Descendants remain ordinary 60-second sleepers: killing their parent
    cannot cooperatively clean them and falsely prove a successful tree stop.
    Only the outer test's AFTER-verdict cleanup sends EOF to a live writer.
    """
    with ExitStack() as owned:
        if child_command is not None:
            # Do not share the writer's pending cleanup-pipe read with the
            # descendant's Windows stdin initialization.
            child = subprocess.Popen(child_command, stdin=subprocess.DEVNULL)
            owned.callback(_cleanup_spawned, child)
        api = pc._RealWinApi()
        handle = api.open(os.getpid())
        owned.callback(api.close, handle)
        creation = api.creation(handle)
        if type(creation) is not int or creation <= 0:
            raise OSError("writer original identity unavailable")
        path = Path(pid_path)
        # Publish the self-sampled original birth BEFORE the atomic PID-ready
        # marker. The writer is still alive; the reader must pin and match it.
        path.with_suffix(".birth").write_text(f"win:{creation}", encoding="ascii")
        ready = path.with_suffix(".ready")
        ready.write_text(str(os.getpid()), encoding="ascii")
        ready.replace(path)
        if cooperative:
            from threading import Event, Thread
            eof = Event()
            def read_eof():
                try:
                    sys.stdin.buffer.read(1)
                finally:
                    eof.set()
            Thread(target=read_eof, daemon=True).start()
            eof.wait(60)
        else:
            time.sleep(60)


def _writer_command(path, child_command=None, *, cooperative=False, executable=None):
    # Use the exact same helpers exercised by the fakes, without importing
    # pytest in the base interpreter. Nested repr preserves Windows paths.
    source = ("import os,subprocess,sys,time\nfrom pathlib import Path\n"
              "from contextlib import ExitStack\nfrom bzs import process_control as pc\n"
              + inspect.getsource(_cleanup_spawned) + "\n"
              + inspect.getsource(_owned_writer) + "\n"
              + f"_owned_writer({str(path)!r}, {child_command!r}, {cooperative!r})\n")
    return [executable or sys._base_executable, "-c", source]


class _OwnedWindowsProcesses(ExitStack):
    """Test-only RAII: original handles/anchors, never postmortem authority."""

    def __init__(self):
        super().__init__()
        self.api = pc._RealWinApi()
        self.cleanup_deadline = None
        self.members = ExitStack()
        # Let a still-running writer reclaim untransferred children BEFORE
        # member rescue can kill it. Pinned handles survive both phases.
        self.callback(self.members.close)

    def spawn(self, command, *, cooperative=False, **kw):
        proc = subprocess.Popen(command, **kw)
        self.callback(_cleanup_spawned, proc)  # before identity queries/asserts
        proc._bzs_cooperative = cooperative
        creation = self.api.creation(proc._handle)  # creation handle, NOT pid
        if type(creation) is not int or creation <= 0:
            raise OSError("spawned original identity unavailable")
        proc._bzs_birth = f"win:{creation}"
        return proc

    def pin_writer(self, path):
        pid = _read_pid(path)
        birth = Path(path).with_suffix(".birth").read_text(encoding="ascii")
        if not _valid_win_birth(birth):
            raise OSError("writer original identity unavailable")
        handle = self.api.open(pid)
        self.members.callback(self.api.close, handle)  # failed handshake closes
        creation = self.api.creation(handle)
        if f"win:{creation}" != birth or self.api.exited(handle):
            raise OSError("writer original identity not alive at handshake")
        self.members.callback(self._cleanup_original, pid, birth, handle)
        return pid, birth

    def _cleanup_original(self, pid, birth, handle):
        # The continuously-held query handle was matched to the writer's own
        # original birth. A fresh PID query can ONLY suppress signalling.
        if self.api.exited(handle):
            return
        current = pc.birth_identity(pid)
        if current is None or (_valid_win_birth(current) and current != birth):
            return
        if current != birth:
            raise RuntimeError(f"pid {pid}: cleanup identity unknown")
        if self.cleanup_deadline is None:
            self.cleanup_deadline = time.monotonic() + 10
        with ExitStack() as release:
            termination = self.api.open_terminate(pid)
            release.callback(self.api.close, termination)
            remaining = self.cleanup_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"pid {pid}: cleanup deadline exceeded")
            self.api.terminate(termination)
            if self.api.wait(handle, remaining * 1000) != "signaled":
                raise RuntimeError(f"pid {pid}: cleanup exit unconfirmed")


def _assert_original_gone(pid, birth, deadline):
    gone, last = await_original_identity_gone(
        lambda: pc.birth_identity(pid), birth, deadline)
    assert gone, (f"pid {pid}: original identity {birth!r} not confirmed gone "
                  f"at deadline (last sample {last!r})")


@pytest.mark.skipif(os.name != "nt", reason="real Windows process tree")
class TestRealWindowsProcesses:
    def _spawn_chain(self, tmp_path, owned):
        leaf, real = tmp_path / "leaf.pid", tmp_path / "real.pid"
        command = _writer_command(real, _writer_command(leaf),
                                  cooperative=True, executable=sys.executable)
        proc = owned.spawn(command, cooperative=True, stdin=subprocess.PIPE,
                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
        real_pid, real_birth = owned.pin_writer(real)
        leaf_pid, leaf_birth = owned.pin_writer(leaf)
        return proc, real_pid, leaf_pid, real_birth, leaf_birth

    def _spawn_pair(self, tmp_path, owned):
        child = tmp_path / "child.pid"
        parent = owned.spawn(
            _writer_command(tmp_path / "parent.pid", _writer_command(child), cooperative=True),
            cooperative=True, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        child_pid, child_birth = owned.pin_writer(child)
        return parent, child_pid, child_birth

    def test_stop_owned_kills_the_whole_real_tree(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        with _OwnedWindowsProcesses() as owned:
            proc, real_pid, leaf_pid, real_birth, leaf_birth = self._spawn_chain(tmp_path, owned)
            record = {"version": 1, "name": "worker", "pid": proc.pid,
                      "birth": proc._bzs_birth, "port": 8611, "state": "ready"}
            pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
            pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")
            assert pc.stop_owned("worker", timeout=10) is True
            deadline = time.monotonic() + 8
            for pid, birth in ((real_pid, real_birth), (leaf_pid, leaf_birth)):
                _assert_original_gone(pid, birth, deadline)
            assert not pc.record_path("worker").exists()
        # Rescue is after EVERY verdict, including record retention/deletion.

    def test_handleless_creation_matches_handle_query_on_real_processes(self, tmp_path):
        # NtQuerySystemInformation and GetProcessTimes agree for both original
        # members; equal creation ticks remain possible and are not excluded.
        with _OwnedWindowsProcesses() as owned:
            parent, child_pid, child_birth = self._spawn_pair(tmp_path, owned)
            parent_free = owned.api.creation_without_handle(parent.pid)
            child_free = owned.api.creation_without_handle(child_pid)
            assert parent_free == int(parent._bzs_birth[4:])
            assert child_free == int(child_birth[4:])
            assert child_free >= parent_free

    def test_unrelated_process_survives_a_tree_stop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        with _OwnedWindowsProcesses() as owned:
            bystander = owned.spawn(
                [sys._base_executable, "-c", "import time;time.sleep(60)"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
            proc, real_pid, leaf_pid, real_birth, leaf_birth = self._spawn_chain(tmp_path, owned)
            assert pc._win_terminate_tree(proc.pid, int(proc._bzs_birth[4:]), deadline_s=10) == []
            deadline = time.monotonic() + 5
            for pid, birth in ((real_pid, real_birth), (leaf_pid, leaf_birth)):
                _assert_original_gone(pid, birth, deadline)
            assert pc.birth_identity(bystander.pid) == bystander._bzs_birth
            assert bystander.poll() is None

    def test_dead_root_candidates_surface_and_record_is_kept(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        with _OwnedWindowsProcesses() as owned:
            # No venv stub: keep the child's original query handle across the
            # intentional parent death. A later PID reuser is NOT survival.
            parent, child_pid, child_birth = self._spawn_pair(tmp_path, owned)
            parent.kill()  # Popen's original creation handle
            parent.wait(timeout=5)
            assert pc.birth_identity(child_pid) == child_birth
            record = {"version": 1, "name": "worker", "pid": parent.pid,
                      "birth": parent._bzs_birth, "port": 8611, "state": "ready"}
            pc.record_path("worker").parent.mkdir(parents=True, exist_ok=True)
            pc.record_path("worker").write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(RuntimeError):
                pc.stop_owned("worker", timeout=5)
            assert pc.record_path("worker").exists()
