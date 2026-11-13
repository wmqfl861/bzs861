#!/usr/bin/env python3
"""CL-11 item 4: clang-format gate over CHANGED LINES only.

Checks that lines ADDED relative to a base ref (origin/main by default)
are clang-format clean, without demanding a full-tree reformat: a full
reformat would invalidate the file:line references in docs/ and
validation/ and conflict with in-flight branches (review-main CL-11
item 4). If a one-shot full reformat is ever wanted, it must be a
separate commit after the CL-01 merge with .git-blame-ignore-revs.

Why not `git clang-format`: the git-clang-format python tool is not
installed in the environment that authored this gate, so this script
implements the equivalent with just a clang-format binary plus the
standard library: it diffs the working tree against
merge-base(HEAD, base), and for every changed C/C++ file with added
lines runs clang-format --style=file and reports only reformat hunks
that intersect an added line. Pre-existing violations on untouched
lines are ignored by design.

Notes / limitations:
- "Changed" means added lines in tracked files (staged or unstaged);
  untracked new files are not covered until committed.
- validation/ is skipped: archived evidence is byte-preserved (CL-23),
  not working source.

Exit codes: 0 clean (including trivially: no changed C/C++ lines),
1 violations found, 2 tooling/usage error.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys

CXX_EXTS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inl"}
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def die(msg: str) -> None:
    print(f"check_changed_format: error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
    )
    if proc.returncode != 0:
        die(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def added_line_set(repo: str, base: str, path: str) -> set[int]:
    """Line numbers (1-based, working-tree numbering) added vs base in path."""
    added: set[int] = set()
    for line in git(repo, "diff", "-U0", base, "--", path).splitlines():
        m = HUNK_RE.match(line)
        if not m:
            continue
        start, count = int(m.group(3)), int(m.group(4) or "1")
        if count > 0:  # count == 0 is a pure deletion: no added lines
            added.update(range(start, start + count))
    return added


def parse_hunks(diff_lines):
    """Yield (header, touched_original_lines, body_lines) from a unified diff."""
    header = None
    touched: set[int] = set()
    body: list[str] = []
    for line in diff_lines:
        m = HUNK_RE.match(line)
        if m:
            if header is not None:
                yield header, touched, body
            old_start, old_count = int(m.group(1)), int(m.group(2) or "1")
            if old_count > 0:
                touched = set(range(old_start, old_start + old_count))
            else:
                # Pure insertion by clang-format between original lines
                # old_start and old_start+1; both anchor lines count.
                touched = {old_start, old_start + 1}
            header = line
            body = []
        elif header is not None:
            body.append(line)
    if header is not None:
        yield header, touched, body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="clang-format gate over changed lines only (CL-11 item 4)")
    ap.add_argument("--base", default="origin/main",
                    help="ref to diff against (default: origin/main)")
    ap.add_argument("--clang-format", default="clang-format",
                    help="clang-format binary (default: from PATH)")
    ap.add_argument("--repo", default=".", help="repository root (default: cwd)")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)

    proc = subprocess.run(
        ["git", "-C", repo, "merge-base", "HEAD", args.base],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        die(f"cannot compute merge-base of HEAD and {args.base} "
            f"(is the base ref fetched? CI needs fetch-depth: 0 on checkout):\n{proc.stderr.strip()}")
    base = proc.stdout.strip()

    changed = [p for p in git(repo, "diff", "--name-only", "-z", base).split("\0") if p]
    cxx_files = [
        p for p in changed
        if not p.startswith("validation/")  # byte-preserved evidence (CL-23)
        and os.path.splitext(p)[1].lower() in CXX_EXTS
        and os.path.isfile(os.path.join(repo, p))
    ]
    if not cxx_files:
        print(f"changed-line clang-format gate: PASS "
              f"(no changed C/C++ files vs {args.base} at {base[:12]})")
        return 0

    violations = []
    checked_files = 0
    checked_lines = 0
    for path in cxx_files:
        added = added_line_set(repo, base, path)
        if not added:
            continue
        fmt = subprocess.run(
            [args.clang_format, "--style=file", path],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="surrogateescape",
        )
        if fmt.returncode != 0:
            die(f"clang-format failed on {path}:\n{fmt.stderr.strip()}")
        with open(os.path.join(repo, path), encoding="utf-8",
                  errors="surrogateescape", newline="") as f:
            orig = f.read().splitlines()
        checked_files += 1
        checked_lines += len(added)
        for header, touched, body in parse_hunks(difflib.unified_diff(
                orig, fmt.stdout.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=0)):
            hit = sorted(touched & added)
            if hit:
                violations.append((path, hit[0], header, body))

    if violations:
        print("changed-line clang-format gate: FAIL")
        print(f"base: {args.base} (merge-base {base[:12]})")
        for path, line, header, body in violations:
            print(f"\n{path}:{line}: changed line(s) not clang-format clean")
            print(f"  {header}")
            for b in body:
                print(f"  {b}")
        bad_files = sorted({p for p, _, _, _ in violations})
        print(f"\n{len(bad_files)} file(s), {len(violations)} hunk(s) violate .clang-format on changed lines.")
        print("Reformat the changed regions only; do not reformat untouched lines.")
        return 1

    print(f"changed-line clang-format gate: PASS "
          f"({checked_files} changed C/C++ file(s), {checked_lines} added line(s) checked "
          f"vs {args.base} at {base[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
