#!/usr/bin/env python3
"""Regressions for tools/check_changed_format.py (CL-11 item 4 repair).

Each test builds a real temporary git repository (never this repository)
and asserts process exit codes, the inspected added-line count, and that
the gate leaves every worktree byte unchanged -- tracked files, the
byte-preserved validation/ tree, and an untracked sentinel included.

Six reviewer defects covered by dedicated RED-capable tests:
  D1 positional @filename acted as an LLVM response file and allowed
     -i writes (tests: at-prefix stdin/not-response, at-prefix checked).
  D2 origin/main == HEAD degraded a main push to a vacuous PASS
     (test_push_main_when_origin_main_equals_head...).
  D3 R100 rename was treated as a whole new file
     (test_r100_pure_rename..., test_rename_with_edit...; the repos set
     diff.renames=false so per-path diff-limited rename detection, as
     the old gate used, really degrades to whole-file-new).
  D4 str.splitlines counted U+2028/NEL/VT differently from Git's LF-only
     numbering and skipped real added violations
     (test_unicode_*_does_not_shift_line_numbers).
  D5 incomplete formatting defaulted to exit 0 and a missing formatter
     produced a traceback exit 1 (test_incomplete_format..., 
     test_missing_formatter..., test_wrong_formatter_major...).
  D6 the formatter major was not pinned (test_wrong_formatter_major...).

Red/green harness: the suite always tests the in-repo checker. To watch
the OLD gate fail these tests, run the same suite with
BZS_CHECKER_UNDER_TEST=<old checker> BZS_LEGACY_IFACE=1 -- run_gate then
points the old script at --base origin/main after positioning
refs/remotes/origin/main the way the old CI did. Commands and counts are
recorded in the task commit; native Linux clang-format-18 CI runs the
suite before the gate (merge gate), local Windows evidence uses the
hash-verified official clang-format 18 wheel and is labeled as such.

Usage: python tools/test_check_changed_format.py --clang-format <binary>
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.environ.get("BZS_CHECKER_UNDER_TEST") or os.path.join(
    HERE, "check_changed_format.py")
LEGACY = bool(os.environ.get("BZZS_LEGACY_IFACE") or os.environ.get("BZS_LEGACY_IFACE"))
CLANG_FORMAT = os.environ.get("BZS_TEST_CLANG_FORMAT", "")
SHA40 = re.compile(r"^[0-9a-f]{40}$")

BAD_CPP = b"int   bad=2;\n"          # 'int bad = 2;' under LLVM spacing
CLEAN_CPP = b"int good = 1;\n"


def run_git(repo, *args, input_bytes=None):
    p = subprocess.run(["git", "-C", repo, *args], input=input_bytes,
                       capture_output=True)
    if p.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} rc={p.returncode}: "
            f"{p.stderr.decode('utf-8', 'replace')}")
    return p


def write(repo, rel, data):
    full = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


def run_gate_on(repo, checker, cf, event):
    argv = [sys.executable, "-I", checker, "--repo", repo,
            "--event-file", "-", "--clang-format", cf]
    return subprocess.run(argv, input=json.dumps(event).encode(),
                          capture_output=True)


def repo_tree_state(repo):
    state = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, repo).replace("\\", "/")
            with open(full, "rb") as fh:
                state[rel] = hashlib.sha256(fh.read()).hexdigest()
    return state


def make_fake_formatter(td, version_line):
    if os.name == "nt":
        path = os.path.join(td, "fake-clang-format.bat")
        with open(path, "w", newline="\r\n") as f:
            f.write(f"@echo off\r\necho {version_line}\r\nexit /b 0\r\n")
        return path
    path = os.path.join(td, "fake-clang-format")
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\necho '{version_line}'\nexit 0\n")
    os.chmod(path, 0o755)
    return path


FAKE_RESPONDER = r'''
import re
import sys

args = []
for a in sys.argv[1:]:
    if a.startswith("@"):
        with open(a[1:]) as f:
            args.extend(f.read().split())
    else:
        args.append(a)
if "--version" in args:
    print("clang-format version 18.0.0-fake-responsefile")
    sys.exit(0)
if "-i" in args:
    for path in args[args.index("-i") + 1:]:
        try:
            data = open(path).read()
        except OSError:
            continue
        open(path, "w").write(re.sub(r"(\w)\s*=\s*(\w)", r"\1 = \2", data))
    sys.exit(0)
# Format mode: report one trivial replacement so violations are visible.
sys.stdout.write("<?xml version='1.0'?>\n"
                 "<replacements xml:space='preserve' "
                 "incomplete_format='false'>\n"
                 "<replacement offset='0' length='0'> </replacement>\n"
                 "</replacements>\n")
'''


def make_response_expanding_formatter(td):
    """Fake formatter emulating LLVM @response-file expansion plus -i.

    The real clang-format 18.1.8 Windows wheel does not expand @files
    (verified: the content is formatted as source), while LLVM documents
    [@<file>] and the reviewer reproduced the -i write on their Linux
    formatter. This double keeps the invocation-path regression honest
    on Windows; the native Linux 18 CI run exercises the real binary.
    """
    script = os.path.join(td, "fake_responder.py")
    with open(script, "w") as f:
        f.write(FAKE_RESPONDER)
    exe = sys.executable
    if os.name == "nt":
        path = os.path.join(td, "fake-responder.bat")
        with open(path, "w", newline="\r\n") as f:
            f.write(f'@echo off\r\n"{exe}" -I "{script}" %*\r\n'
                    f"exit /b %errorlevel%\r\n")
        return path
    path = os.path.join(td, "fake-responder")
    with open(path, "w") as f:
        f.write(f'#!/bin/sh\nexec "{exe}" -I "{script}" "$@"\n')
    os.chmod(path, 0o755)
    return path


class GateTest(unittest.TestCase):
    """Base: temp repo, base commit, sentinel, no-write enforcement."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="ccf-test-")
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.repo = os.path.join(self.td, "repo")
        os.makedirs(self.repo)
        run_git(self.repo, "init")
        run_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        run_git(self.repo, "config", "user.email", "t@example.invalid")
        run_git(self.repo, "config", "user.name", "t")
        run_git(self.repo, "config", "commit.gpgsign", "false")
        run_git(self.repo, "config", "core.autocrlf", "false")
        run_git(self.repo, "config", "gc.auto", "0")
        write(self.repo, ".clang-format",
              b"BasedOnStyle: LLVM\nIndentWidth: 2\nColumnLimit: 100\n")
        write(self.repo, "validation/archived.cpp", b"int   keep=1;\n")
        self.base = self.commit([".clang-format", "validation/archived.cpp"], "base")
        # Untracked guard: created after the base commit, never added.
        write(self.repo, "untracked-sentinel.txt", b"sentinel payload\n")
        self.legacy_main_tip = None

    def commit(self, paths=(), msg="m", rm=()):
        for p in rm:
            run_git(self.repo, "rm", "-q", "--", p)
        for p in paths:
            run_git(self.repo, "add", "--", p)
        run_git(self.repo, "commit", "-q", "--allow-empty", "-m", msg)
        return run_git(self.repo, "rev-parse", "HEAD").stdout.strip().decode()

    def run_gate(self, event, cf=None):
        cf = cf or CLANG_FORMAT
        if LEGACY:
            tip = self.legacy_main_tip or self._legacy_tip(event)
            if tip and SHA40.match(tip):
                run_git(self.repo, "update-ref", "refs/remotes/origin/main", tip)
            argv = [sys.executable, "-I", CHECKER, "--repo", self.repo,
                    "--clang-format", cf, "--base", "origin/main"]
            return subprocess.run(argv, capture_output=True)
        argv = [sys.executable, "-I", CHECKER, "--repo", self.repo,
                "--event-file", "-", "--clang-format", cf]
        return subprocess.run(argv, input=json.dumps(event).encode(),
                              capture_output=True)

    @staticmethod
    def _legacy_tip(event):
        for key in ("before", "base", "after", "head"):
            value = event.get(key)
            if isinstance(value, str) and SHA40.match(value):
                return value
        return None

    def gate(self, event, cf=None):
        before = repo_tree_state(self.repo)
        proc = self.run_gate(event, cf=cf)
        after = repo_tree_state(self.repo)
        self.assertEqual(before, after,
                         "gate must not modify any repository byte "
                         "(tracked, validation/, untracked sentinel)")
        return proc

    def push(self, before, after, ref="refs/heads/main", cf=None):
        return self.gate({"event": "push", "before": before,
                          "after": after, "ref": ref}, cf=cf)

    # ---- plumbing for paths the filesystem cannot hold (newline names)

    def plumbing_add_blob(self, parent, name_bytes, content):
        blob = run_git(self.repo, "hash-object", "-w", "--stdin",
                       input_bytes=content).stdout.strip().decode()
        entries = []
        ls = run_git(self.repo, "ls-tree", "-z", parent + "^{tree}").stdout
        for rec in ls.split(b"\0"):
            if not rec:
                continue
            meta, name = rec.split(b"\t", 1)
            fields = meta.decode().split()
            mode = "40000" if fields[0] == "040000" else fields[0]
            entries.append((mode, name, fields[2]))
        entries.append(("100644", name_bytes, blob))
        entries.sort(key=lambda e: e[1])
        payload = b"".join(mode.encode() + b" " + name + b"\0" +
                           bytes.fromhex(oid) for mode, name, oid in entries)
        tree = run_git(self.repo, "hash-object", "-t", "tree", "-w", "--stdin",
                       input_bytes=payload).stdout.strip().decode()
        return run_git(self.repo, "commit-tree", tree, "-p", parent,
                       "-m", "plumbing head").stdout.strip().decode()


class DefectTests(GateTest):

    def test_at_response_file_injection_writes_nothing(self):
        """D1 red/green core: no repo path is ever a positional argument.

        LLVM reads an argument starting with '@' as a response file whose
        path is the argument minus the '@'. The old gate passed the raw
        repository path positionally, so checking the tracked '@pwn.cpp'
        made the formatter read the attacker-planted 'pwn' payload next
        to it as OPTIONS ('-i validation/evil-target.cpp') and rewrite
        the archived validation file in place -- the no-write assertion
        below is the red signal. The new gate sends the source over
        stdin and the only filename lives inside --assume-filename=...,
        which the expander treats as data.

        The fake formatter emulates @response-file expansion plus -i:
        the real clang-format 18.1.8 Windows wheel does not expand @files
        (verified: the content is formatted as source), while LLVM
        documents [@<file>] and the reviewer reproduced the -i write on
        their formatter. Native Linux clang-format-18 CI runs the real
        binary; the invariant tested here -- no positional repo path --
        is what makes the real expansion harmless.
        """
        write(self.repo, "validation/evil-target.cpp", b"int   evil=1;\n")
        write(self.repo, "@pwn.cpp", BAD_CPP)
        write(self.repo, "pwn.cpp", b"-i\nvalidation/evil-target.cpp\n")
        head = self.commit(["validation/evil-target.cpp", "@pwn.cpp", "pwn.cpp"],
                           "head")
        fake = make_response_expanding_formatter(self.td)
        p = self.push(self.base, head, cf=fake)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"@pwn.cpp:1", p.stdout)
        self.assertRegex(p.stdout, rb"3 added line\(s\) checked")

    def test_at_prefixed_source_goes_via_stdin_not_response_file(self):
        """D1: '@pwn.cpp' must be data on stdin, never a response file.

        The file body deliberately contains '-i' plus a validation path.
        The old gate passed the path as a positional argument, so the
        formatter expanded it as a response file and rewrote
        validation/evil-target.cpp in place; the no-write assertion is
        the red signal there.
        """
        write(self.repo, "validation/evil-target.cpp", b"int   evil=1;\n")
        write(self.repo, "@pwn.cpp", b"-i\nvalidation/evil-target.cpp\n")
        head = self.commit(["validation/evil-target.cpp", "@pwn.cpp"], "head")
        p = self.push(self.base, head)
        # The body is opaque data on stdin. If clang-format 18 can parse
        # it, the gate reports ordinary style violations (rc 1); if it
        # cannot, that is an incomplete format (rc 2). Either way the
        # '-i' line was never executed as an argument and no repository
        # byte changed (asserted by gate() for every run).
        self.assertIn(p.returncode, (1, 2), p.stdout + p.stderr)

    def test_at_prefixed_file_still_checked_normally(self):
        write(self.repo, "@ok.cpp", BAD_CPP)
        head = self.commit(["@ok.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"@ok.cpp:1", p.stdout)

    def test_push_main_when_origin_main_equals_head_still_checks(self):
        """D2: before..after directly; no merge-base vacuous pass."""
        write(self.repo, "src/new.cpp", b"int ok;\n" + BAD_CPP)
        head = self.commit(["src/new.cpp"], "head")
        # Post-push CI state: the remote tracking ref already equals HEAD.
        self.legacy_main_tip = head
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn(b"src/new.cpp:2", p.stdout)
        self.assertRegex(p.stdout, rb"2 added line\(s\) checked")

    def test_r100_pure_rename_checks_no_lines(self):
        """D3: pure rename adds zero lines, whatever git config says."""
        run_git(self.repo, "config", "diff.renames", "false")
        write(self.repo, "old.cpp", b"int   ugly=1;\n")  # badly spaced by design
        mid = self.commit(["old.cpp"], "add old")
        run_git(self.repo, "mv", "old.cpp", "renamed.cpp")
        head = self.commit(["renamed.cpp"], "rename")
        p = self.push(mid, head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, rb"0 added line\(s\) checked")

    def test_rename_with_edit_counts_only_edited_lines(self):
        """D3/D8: rename+edit is judged on the new path's added lines."""
        run_git(self.repo, "config", "diff.renames", "false")
        write(self.repo, "old.cpp", b"int   ugly=1;\n")  # pre-existing violation
        mid = self.commit(["old.cpp"], "add old")
        run_git(self.repo, "mv", "old.cpp", "moved.cpp")
        write(self.repo, "moved.cpp", b"int   ugly=1;\n" + CLEAN_CPP)
        head = self.commit(["moved.cpp"], "rename+clean edit")
        p = self.push(mid, head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, rb"1 added line\(s\) checked")
        # Same shape but the edited-in line violates:
        write(self.repo, "moved.cpp", b"int   ugly=1;\n" + BAD_CPP)
        head2 = self.commit(["moved.cpp"], "rename+bad edit")
        p2 = self.push(mid, head2)
        self.assertEqual(p2.returncode, 1, p2.stdout)
        self.assertIn(b"moved.cpp:2", p2.stdout)

    def test_unicode_line_separator_does_not_shift_line_numbers(self):
        """D4: U+2028 on an earlier line must not shift Git's LF numbering."""
        sep = "a\u2028b".encode()
        write(self.repo, "src/a.cpp", b"int keep = 1;  // note " + sep + b"\n")
        mid = self.commit(["src/a.cpp"], "clean with unicode sep")
        write(self.repo, "src/a.cpp",
              b"int keep = 1;  // note " + sep + b"\n" + BAD_CPP)
        head = self.commit(["src/a.cpp"], "add bad line")
        p = self.push(mid, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/a.cpp:2", p.stdout)

    def test_nel_and_vertical_tab_do_not_shift_line_numbers(self):
        for sep in ("a\u0085b".encode(), b"a\x0bb"):
            with self.subTest(sep=sep):
                write(self.repo, "src/a.cpp", b"int keep = 1;  // note " + sep + b"\n")
                mid = self.commit(["src/a.cpp"], "clean")
                write(self.repo, "src/a.cpp",
                      b"int keep = 1;  // note " + sep + b"\n" + BAD_CPP)
                head = self.commit(["src/a.cpp"], "add bad line")
                p = self.push(mid, head)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(b"src/a.cpp:2", p.stdout)

    def test_incomplete_format_is_tooling_error_not_silent_pass(self):
        """D5: incomplete formatting must exit 2, never silently pass."""
        write(self.repo, "src/broken.cpp", b"int f() {\n  if (\n")
        head = self.commit(["src/broken.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn(b"incomplete", p.stderr.lower())

    def test_missing_formatter_is_diagnostic_exit2_without_traceback(self):
        """D5: a missing binary is a diagnostic exit 2, not a traceback."""
        write(self.repo, "src/x.cpp", BAD_CPP)
        head = self.commit(["src/x.cpp"], "head")
        p = self.push(self.base, head,
                      cf=os.path.join(self.td, "no-such", "clang-format-18"))
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn(b"not found", p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)

    def test_wrong_formatter_major_rejected(self):
        """D6: only major 18 runs; local 19 is never accepted as 18."""
        write(self.repo, "src/x.cpp", BAD_CPP)
        head = self.commit(["src/x.cpp"], "head")
        fake = make_fake_formatter(self.td, "clang-format version 19.1.5")
        p = self.push(self.base, head, cf=fake)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn(b"19", p.stderr)
        self.assertIn(b"18", p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)


class TopologyTests(GateTest):

    def test_pr_event_uses_event_base_head(self):
        write(self.repo, "src/pr.cpp", BAD_CPP)
        head = self.commit(["src/pr.cpp"], "head")
        p = self.gate({"event": "pull_request", "base": self.base, "head": head})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/pr.cpp:1", p.stdout)

    def test_pr_synthetic_merge_head_is_never_the_checked_head(self):
        """Ruling 1/2: bytes come from the event head, not github.sha."""
        write(self.repo, "src/x.cpp", BAD_CPP)
        head = self.commit(["src/x.cpp"], "bad head")
        # Synthetic merge checkout: HEAD resolves the conflict CLEANLY.
        write(self.repo, "src/x.cpp", CLEAN_CPP)
        clean_tree = run_git(self.repo, "write-tree").stdout.strip().decode()
        merge = run_git(self.repo, "commit-tree", clean_tree,
                        "-p", self.base, "-p", head,
                        "-m", "synthetic merge").stdout.strip().decode()
        run_git(self.repo, "reset", "-q", "--hard", merge)
        p = self.gate({"event": "pull_request", "base": self.base, "head": head})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/x.cpp:1", p.stdout)

    def test_batch_push_checks_union_of_added_lines(self):
        write(self.repo, "src/b1.cpp", BAD_CPP)
        mid = self.commit(["src/b1.cpp"], "bad commit")
        write(self.repo, "src/b2.cpp", CLEAN_CPP)
        head = self.commit(["src/b2.cpp"], "clean commit")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/b1.cpp:1", p.stdout)

    def test_force_push_compares_before_after_directly(self):
        write(self.repo, "src/x.cpp", b"int   old=1;\n")
        before = self.commit(["src/x.cpp"], "old tip")
        run_git(self.repo, "reset", "-q", "--hard", self.base)
        write(self.repo, "src/x.cpp", b"int   new=1;\n")
        after = self.commit(["src/x.cpp"], "rewritten tip")
        p = self.push(before, after)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"before..after directly", p.stdout)
        self.assertIn(b"src/x.cpp:1", p.stdout)

    def test_new_branch_before_zero_uses_declared_main_baseline(self):
        write(self.repo, "src/feat.cpp", BAD_CPP)
        head = self.commit(["src/feat.cpp"], "feature")
        run_git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.legacy_main_tip = self.base
        p = self.push("0" * 40, head, ref="refs/heads/feature/new")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"baseline policy", p.stdout)
        self.assertIn(b"merge-base", p.stdout)

    def test_new_branch_before_zero_without_main_is_error2(self):
        write(self.repo, "src/feat.cpp", CLEAN_CPP)
        head = self.commit(["src/feat.cpp"], "feature")
        p = self.push("0" * 40, head, ref="refs/heads/feature/new")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn(b"main", p.stderr)

    def test_new_branch_unrelated_history_is_error2(self):
        run_git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.legacy_main_tip = self.base
        orphan_tree = run_git(self.repo, "write-tree").stdout.strip().decode()
        orphan = run_git(self.repo, "commit-tree", orphan_tree,
                         "-m", "orphan").stdout.strip().decode()
        p = self.push("0" * 40, orphan, ref="refs/heads/feature/new")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn(b"no common ancestor", p.stderr)

    def test_first_main_push_empty_tree_checks_whole_tree(self):
        write(self.repo, "src/whole.cpp", BAD_CPP)
        head = self.commit(["src/whole.cpp"], "first main")
        p = self.push("0" * 40, head, ref="refs/heads/main")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"EMPTY TREE", p.stdout)
        self.assertIn(b"src/whole.cpp:1", p.stdout)

    def test_first_main_push_clean_tree_passes(self):
        write(self.repo, "src/whole.cpp", CLEAN_CPP)
        head = self.commit(["src/whole.cpp"], "first main clean")
        p = self.push("0" * 40, head, ref="refs/heads/main")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, rb"1 added line\(s\) checked")

    def test_missing_before_object_without_remote_is_error2(self):
        write(self.repo, "src/x.cpp", CLEAN_CPP)
        head = self.commit(["src/x.cpp"], "head")
        missing = "1" * 40
        p = self.push(missing, head)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn(missing[:12].encode(), p.stderr)

    def test_missing_object_is_fetched_once_then_checked(self):
        """Ruling 2: missing event objects are fetched by exact SHA.

        Mirrors CI after a force push: the local side only has the new
        main line, the event's 'before' tip was never fetched, and the
        server still serves it (here as an advertised tip ref, the way
        GitHub advertises refs/pull/* heads).
        """
        seed = os.path.join(self.td, "seed")
        os.makedirs(seed)
        run_git(seed, "init")
        run_git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
        run_git(seed, "config", "user.email", "t@example.invalid")
        run_git(seed, "config", "user.name", "t")
        write(seed, "a.cpp", b"int   x=1;\n")
        run_git(seed, "add", "a.cpp")
        run_git(seed, "commit", "-qm", "one")
        run_git(seed, "commit", "-q", "--allow-empty", "-m", "before tip")
        before = run_git(seed, "rev-parse", "HEAD").stdout.strip().decode()
        run_git(seed, "reset", "-q", "--hard", "HEAD~1")
        write(seed, "a.cpp", b"int   y=1;\n")
        run_git(seed, "commit", "-qam", "rewritten tip")
        after = run_git(seed, "rev-parse", "HEAD").stdout.strip().decode()
        remote = os.path.join(self.td, "remote.git")
        run_git(seed, "init", "--bare", remote)
        run_git(seed, "remote", "add", "origin", remote.replace("\\", "/"))
        run_git(seed, "push", "-q", "origin", "main")
        run_git(seed, "push", "-q", "origin", before + ":refs/heads/kept-oldtip")
        run_git(seed, "push", "-q", "--force", "origin", after + ":refs/heads/main")
        # The gate's repository fetched only the rewritten main: it has
        # 'after' but never had 'before'.
        gate_repo = self.repo
        run_git(gate_repo, "remote", "add", "origin", remote.replace("\\", "/"))
        run_git(gate_repo, "fetch", "-q", "origin", "main")
        p = run_gate_on(gate_repo, CHECKER, CLANG_FORMAT,
                        {"event": "push", "before": before, "after": after,
                         "ref": "refs/heads/main"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"fetched missing commit", p.stdout)
        self.assertIn(b"a.cpp:1", p.stdout)

    def test_invalid_event_fields_are_error2(self):
        for event in (
            {"event": "push", "before": "1" * 39, "after": self.base},
            {"event": "push", "before": self.base, "after": "HEAD"},
            {"event": "push", "before": self.base},
            {"event": "pull_request", "base": self.base},
            {"event": "ping"},
        ):
            with self.subTest(event=event):
                p = self.gate(event)
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertNotIn(b"Traceback", p.stderr)

    def test_delete_branch_after_zero_is_explicit_skip(self):
        p = self.push(self.base, "0" * 40, ref="refs/heads/feature/gone")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(b"SKIP", p.stdout)

    def test_no_change_push_passes_with_zero_lines(self):
        p = self.push(self.base, self.base)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertRegex(p.stdout, rb"0 added line\(s\) checked")

    def test_not_a_git_repository_is_error2(self):
        plain = os.path.join(self.td, "not-a-repo")
        os.makedirs(plain)
        p = run_gate_on(plain, CHECKER, CLANG_FORMAT,
                        {"event": "push", "before": self.base,
                         "after": self.base, "ref": "refs/heads/main"})
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)


class ParsingTests(GateTest):

    def test_leading_dash_path_checked(self):
        write(self.repo, "-dash.cpp", BAD_CPP)
        head = self.commit(["-dash.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"-dash.cpp:1", p.stdout)

    def test_space_and_tab_paths_checked(self):
        write(self.repo, "a b.cpp", BAD_CPP)
        mid = self.commit(["a b.cpp"], "space path")
        # Tab is not a legal filename character on Windows/NTFS, so the
        # tab path goes through tree plumbing: the gate reads paths from
        # the diff and blobs by OID and never touches the filesystem.
        head = self.plumbing_add_blob(mid, b"c\td.cpp", BAD_CPP)
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"a b.cpp:1", p.stdout)
        self.assertIn(b"c\td.cpp:1", p.stdout)

    def test_newline_path_checked_without_worktree(self):
        """Paths are read from the diff and blobs by OID, never from disk."""
        head = self.plumbing_add_blob(self.base, b"nl\nx.cpp", BAD_CPP)
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"nl\nx.cpp:1", p.stdout)

    def test_crlf_lines_checked_and_clean_crlf_passes(self):
        write(self.repo, "src/crlf.cpp", b"int   bad=1;\r\n")
        head = self.commit(["src/crlf.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/crlf.cpp:1", p.stdout)
        write(self.repo, "src/crlf.cpp", b"int good = 1;\r\n")
        head2 = self.commit(["src/crlf.cpp"], "clean")
        p2 = self.push(head, head2)
        self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)

    def test_missing_final_newline_on_added_line_is_a_violation(self):
        """EOF newline state is preserved byte-for-byte, not manufactured.

        clang-format 18 judging stdin without a final newline decides
        itself whether a trailing-newline replacement exists; the gate
        maps whatever replacements it emits with LF-only numbering and
        never re-splits or normalizes the stream.
        """
        write(self.repo, "src/nolf.cpp", b"int   bad=1")
        head = self.commit(["src/nolf.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"src/nolf.cpp:1", p.stdout)
        write(self.repo, "src/nolf2.cpp", b"int good = 1")
        head2 = self.commit(["src/nolf2.cpp"], "clean but no final LF")
        p2 = self.push(head, head2)
        # The formatter emitted no replacement for this content, so the
        # gate must not invent one: a clean added line stays clean.
        self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
        self.assertRegex(p2.stdout, rb"1 added line\(s\) checked")

    def test_deletions_and_validation_are_excluded(self):
        write(self.repo, "src/del.cpp", CLEAN_CPP)
        mid = self.commit(["src/del.cpp"], "add")
        write(self.repo, "validation/archived.cpp", b"int   worse=1;\n")
        head = self.commit(["validation/archived.cpp"], "touch validation",
                           rm=["src/del.cpp"])
        p = self.push(mid, head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, rb"0 added line\(s\) checked")

    def test_binary_cpp_file_skipped(self):
        write(self.repo, "src/bin.cpp", b"int a;\x00\x01\x02\n")
        head = self.commit(["src/bin.cpp"], "head")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, rb"0 added line\(s\) checked")

    def test_config_snapshot_comes_from_exact_head(self):
        """The style is the head tree's .clang-format, not the worktree's."""
        write(self.repo, "src/t.cpp", b"int good = 1;\n")
        head = self.commit(["src/t.cpp"], "head")
        # Worktree config now disagrees with the head config; the gate
        # must keep judging with the head config (no worktree reads).
        write(self.repo, ".clang-format",
              b"BasedOnStyle: LLVM\nIndentWidth: 4\nColumnLimit: 20\n")
        p = self.push(self.base, head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


def main():
    global CLANG_FORMAT
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clang-format", default=CLANG_FORMAT or None,
                    help="clang-format 18 binary used by the tests")
    ap.add_argument("unittest_args", nargs="*")
    ns, rest = ap.parse_known_args()
    if not ns.clang_format:
        ap.error("--clang-format is required (or set BZS_TEST_CLANG_FORMAT)")
    CLANG_FORMAT = ns.clang_format
    unittest.main(argv=[sys.argv[0], *ns.unittest_args], verbosity=2)


if __name__ == "__main__":
    main()
