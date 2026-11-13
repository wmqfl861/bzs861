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


def await_original_identity_gone(query, anchor, deadline, *, now=time.monotonic,
                                 sleep=time.sleep):
    """Identity-anchored death verdict for one tree member (CL-25 signature-3
    class, ported from launcher_lifecycle eda84e6).

    The anchor is the member's ORIGINAL identity captured at first sight,
    BEFORE the tree is stopped. A sample of None (nothing holds the pid any
    more) OR a different valid identity (the pid was reused) confirms the
    original identity is gone; a sample equal to the anchor keeps waiting; an
    identity-query EXCEPTION never counts as an exit. The loop shares the
    caller's single deadline; one sample after the deadline decides the
    verdict. Returns (gone, last) where `last` is the deciding sample or the
    deciding OSError.
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


class TestAnchoredDeathCheck:
    """Deterministic regressions for the identity-anchored death check ported
    from launcher_lifecycle's CL-25 signature-3 fix (eda84e6): the real-Windows
    tests poll member pids whose ORIGINAL identities were anchored at first
    sight, so a pid later held by a churn reuser reads as a DIFFERENT identity
    and still confirms the original is gone. Fake samplers and a fake clock
    exercise every verdict rule with no real pid churn."""

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
        assert gone is False
        assert last == "win:anchor"

    def test_pid_reuse_different_identity_passes(self):
        # Signature-3 class: the pid is later held by a DIFFERENT process; a
        # different valid identity confirms the original identity is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor", "win:anchor", "win:reuser"]),
            "win:anchor", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last == "win:reuser"

    def test_reuse_before_first_sample_passes(self):
        # The pid got reused BEFORE the first poll: the first query already
        # reports the reuser while the anchor keeps the first-sight identity.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:reuser"]), "win:anchor", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last == "win:reuser"

    def test_none_sample_passes(self):
        # Nothing holds the pid any more: the original identity is gone.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor", None]), "win:anchor", 105.0,
            now=now, sleep=sleep)
        assert gone is True
        assert last is None

    def test_identity_query_error_is_not_an_exit(self):
        # Query exceptions are neither an exit nor a failure; the verdict only
        # lands on a later None/different-identity sample.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed"), OSError("probe failed"), None]),
            "win:anchor", 105.0, now=now, sleep=sleep)
        assert gone is True
        assert last is None

    def test_identity_query_error_at_deadline_does_not_pass(self):
        # Only exceptions until the deadline: the original was never confirmed
        # gone, so the verdict must not pass.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky([OSError("probe failed")]), "win:anchor", 105.0,
            now=now, sleep=sleep)
        assert gone is False
        assert isinstance(last, OSError)

    def test_exit_confirmed_only_by_the_final_sample_passes(self):
        # The anchored identity is sampled for the whole window and the pid is
        # released/reused only at the deciding sample AFTER the deadline: the
        # shared deadline still yields a correct verdict from that one sample.
        now, sleep = self._clock()
        gone, last = await_original_identity_gone(
            self._sticky(["win:anchor"] * 10 + ["win:reuser"]),
            "win:anchor", 101.0, now=now, sleep=sleep)
        assert gone is True
        assert last == "win:reuser"


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
        real_pid, leaf_pid = _read_pid(real), _read_pid(leaf)
        # CL-25 signature-3 class (eda84e6 port): anchor each member's ORIGINAL
        # identity at first sight - the writer is alive here, BEFORE any stop
        # call - so a later churn reuser holding the pid reads as a DIFFERENT
        # identity and still confirms the original is gone.
        real_birth, leaf_birth = pc.birth_identity(real_pid), pc.birth_identity(leaf_pid)
        assert real_birth is not None, "real interpreter not alive at first sight"
        assert leaf_birth is not None, "leaf not alive at first sight"
        return proc, real_pid, leaf_pid, real_birth, leaf_birth

    def test_stop_owned_kills_the_whole_real_tree(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        proc, real_pid, leaf_pid, real_birth, leaf_birth = self._spawn_chain(tmp_path)
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
        for pid, birth in ((real_pid, real_birth), (leaf_pid, leaf_birth)):
            gone, last = await_original_identity_gone(
                lambda pid=pid: pc.birth_identity(pid), birth, deadline)
            if not gone:
                if isinstance(last, OSError):
                    pytest.fail(f"pid {pid}: original identity {birth!r} not confirmed "
                                f"gone; identity query failed at deadline: {last}")
                pytest.fail(f"pid {pid}: original identity {birth!r} still alive at "
                            f"deadline (last sample {last!r})")
        assert not pc.record_path("worker").exists()

    def test_handleless_creation_matches_handle_query_on_real_processes(self, tmp_path):
        # Real-Windows evidence for the exclusion rule's clock: the creation
        # FILETIME read without any handle (NtQuerySystemInformation) equals
        # the one read through a query handle (GetProcessTimes), and a real
        # child spawned by a running parent is never created earlier on either
        # clock - equal ticks are possible because the creation FILETIME has
        # finite granularity, which is exactly why equal-tick entries are
        # never excluded and never signalled.
        api = pc._RealWinApi()
        child_pid_file = str(Path(tmp_path) / "child.pid").replace("\\", "/")
        parent = subprocess.Popen(
            [sys._base_executable, "-c",
             f"import os,subprocess,sys,time;"
             f"p=subprocess.Popen([sys._base_executable,'-c',"
             f"'import time;time.sleep(60)']);"
             f"open({child_pid_file!r},'w').write(str(p.pid));time.sleep(60)"],
            stdout=subprocess.DEVNULL)
        child_pid = None
        try:
            ph = api.open(parent.pid)
            try:
                parent_handle_creation = api.creation(ph)
            finally:
                api.close(ph)
            child_pid = _read_pid(child_pid_file)
            ch = api.open(child_pid)
            try:
                child_handle_creation = api.creation(ch)
            finally:
                api.close(ch)
            parent_free = api.creation_without_handle(parent.pid)
            child_free = api.creation_without_handle(child_pid)
            assert parent_free == parent_handle_creation
            assert child_free == child_handle_creation
            assert child_free >= parent_free     # never earlier; an equal tick is possible
        finally:
            parent.kill()
            parent.wait(timeout=5)
            child_birth = pc.birth_identity(child_pid) if child_pid else None
            if child_birth is not None:
                pc._win_terminate_tree(child_pid, int(child_birth.split(":")[1]), deadline_s=10)

    def test_unrelated_process_survives_a_tree_stop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BZS_HOME", str(tmp_path))
        bystander = subprocess.Popen([sys._base_executable, "-c", "import time;time.sleep(60)"],
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
        proc, real_pid, _leaf_pid, real_birth, _leaf_birth = self._spawn_chain(tmp_path)
        try:
            pc._win_terminate_tree(proc.pid, int(proc._bzs_birth.split(":")[1]), deadline_s=10)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        deadline = time.monotonic() + 5
        gone, last = await_original_identity_gone(
            lambda: pc.birth_identity(real_pid), real_birth, deadline)
        if not gone:
            if isinstance(last, OSError):
                pytest.fail(f"pid {real_pid}: original identity {real_birth!r} not "
                            f"confirmed gone; identity query failed at deadline: {last}")
            pytest.fail(f"pid {real_pid}: original identity {real_birth!r} still alive "
                        f"at deadline (last sample {last!r})")
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
