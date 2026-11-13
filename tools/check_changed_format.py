#!/usr/bin/env python3
"""CL-11 item 4: clang-format gate over CHANGED LINES only (exact-event form).

Checks that lines ADDED by an exact event range are clang-format clean,
without demanding a full-tree reformat (review-main CL-11 item 4; the
binding policy is the 2026-11 format-gate decision log).

Interface -- event values arrive as untrusted JSON and are validated
before any use; they never reach a shell or a positional argument:

  --event-file PATH|-  {"event":"pull_request","base":SHA,"head":SHA}
                      |{"event":"push","before":SHA|40*'0',"after":SHA|40*'0',
                      | "ref":"refs/heads/..."}

  pull_request: compare merge-base(base, head)..head using the EVENT's
    base/head SHAs; the synthetic merge checkout (github.sha) is never
    used as the PR head. Checked bytes and .clang-format both come from
    the exact head tree, never from the (merge-checkout) worktree.
  push: compare before..after directly (covers main pushes, batch
    commits, merges and force pushes; no merge-base, no runtime
    origin/main -- a push whose origin/main equals HEAD therefore cannot
    degrade into a vacuous empty diff).
    after all-zero (branch deletion): explicit SKIP, exit 0.
    before all-zero on the protected main branch: the base is the EMPTY
      TREE, so every existing line counts as added.
    before all-zero on any other ref: the baseline is
      merge-base(protected main, after) and the run says so loudly; that
      is a declared fallback policy, not a historical before. A missing
      main or no common ancestor is a tooling error.
  Missing commit objects required by the event are fetched once from
  --fetch-remote (default origin) by exact SHA, without persisting any
  credentials; if they stay missing the run is a tooling error. There
  are no HEAD^/self fallbacks.

Formatter contract: the pinned major is 18. The major is verified at
startup (CI installs clang-format-18 and passes its explicit path); a
different major is a tooling error, never silently treated as 18, and
local 19.x evidence is never relabeled as 18. Each candidate file's
exact-head blob is piped through stdin; the only filename argument is a
single safe absolute --assume-filename=... inside a temp mirror that
carries the exact-head .clang-format snapshot, so the gate never writes
a repository byte, never reads the worktree, and never passes a
repository path as a positional argument (a leading-@ filename is
therefore inert data, not an LLVM response file, and -i cannot appear).

Deviation from the decision log, recorded here and in the task commit:
clang-format 18 does not implement --fail-on-incomplete-format (that
flag postdates 18). Incomplete formatting is instead enforced from the
same run's --output-replacements-xml result
(incomplete_format="true" => exit 2), which is exactly what that flag
exists for; incomplete input never passes silently.

Diff handling: one global rename-aware
  git diff --raw --patch -z -U0 --no-abbrev --find-renames
      --no-ext-diff --no-textconv base head --
statuses/paths come from the NUL-framed raw records (explicit
--find-renames makes rename detection independent of local git config),
added lines come from the hunk headers, both in new-file numbering; a
pure rename (R100) therefore contributes zero added lines and rename
with edits only counts the edited-in lines. All line arithmetic is
LF-only over bytes; CR and other non-LF control characters stay ordinary
bytes (Python str.splitlines is never used, so U+2028/NEL/VT cannot
shift line numbers) and the end-of-file newline state is preserved.
Blobs are read by object ID, so no path is ever resolved on disk and
nothing can escape the repository root. validation/ is excluded
(byte-preserved evidence, CL-23).

Exit codes: 0 clean / no added C/C++ lines / SKIP, 1 style violations
on added lines, 2 tooling/usage error (bad event, missing object, git
or formatter failure, wrong formatter major, incomplete format).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from bisect import bisect_right

MAJOR = 18
ZERO_SHA = "0" * 40
CXX_EXTS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inl"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
VERSION_RE = re.compile(r"version\s+(\d+)\.", re.IGNORECASE)


def die(msg: str) -> None:
    print(f"check_changed_format: error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(cmd, *, input_bytes=None):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(cmd, input=input_bytes, capture_output=True, env=env)
    except FileNotFoundError:
        die(f"required executable not found: {cmd[0]}")


def git(repo: str, *args: str, input_bytes=None, allow_fail: bool = False):
    p = run(["git", "-C", repo, *args], input_bytes=input_bytes)
    if p.returncode != 0 and not allow_fail:
        die(f"git {' '.join(args)} failed:\n"
            f"{p.stderr.decode('utf-8', 'replace').strip()}")
    return p


def out(p) -> str:
    return p.stdout.decode("utf-8", "surrogateescape")


def verify_formatter_major(cf: str) -> str:
    p = run([cf, "--version"])
    if p.returncode != 0:
        die(f"cannot run {cf} --version (rc={p.returncode}): "
            f"{p.stderr.decode('utf-8', 'replace').strip()}")
    text = (p.stdout + p.stderr).decode("utf-8", "replace")
    m = VERSION_RE.search(text)
    if not m:
        die(f"cannot parse a clang-format version from {text.strip()!r}")
    major = int(m.group(1))
    if major != MAJOR:
        die(f"clang-format major {major} does not match the pinned major {MAJOR} "
            f"({text.strip()}); install clang-format-{MAJOR} or pass "
            f"--clang-format explicitly; refusing to run a different formatter")
    return text.strip()


def load_event(path: str) -> dict:
    try:
        data = sys.stdin.buffer.read() if path == "-" else open(path, "rb").read()
        event = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        die(f"cannot read event JSON from {path!r}: {exc}")
    if not isinstance(event, dict):
        die("event JSON must be a JSON object")
    return event


def sha_field(event: dict, name: str) -> str:
    value = event.get(name)
    if not isinstance(value, str):
        die(f"event field {name!r} is missing or not a string")
    value = value.lower()
    if value != ZERO_SHA and not SHA_RE.match(value):
        die(f"event field {name!r} must be a full 40-hex SHA or 40 zeros, got {value!r}")
    return value


def empty_tree(repo: str) -> str:
    p = git(repo, "hash-object", "-t", "tree", "--stdin", input_bytes=b"")
    return out(p).strip()


def ensure_commit(repo: str, sha: str, remote: str) -> None:
    if git(repo, "cat-file", "-e", f"{sha}^{{commit}}", allow_fail=True).returncode == 0:
        return
    url = git(repo, "remote", "get-url", remote, allow_fail=True)
    if url.returncode == 0:
        git(repo, "fetch", "--quiet", "--no-tags", remote, sha, allow_fail=True)
        if git(repo, "cat-file", "-e", f"{sha}^{{commit}}",
               allow_fail=True).returncode == 0:
            print(f"fetched missing commit {sha[:12]} from {remote} by exact SHA")
            return
    die(f"commit {sha} required by the event is not present locally and could not "
        f"be fetched from remote {remote!r}; fetch the exact event objects "
        f"(CI needs fetch-depth: 0) and retry")


def merge_base(repo: str, a: str, b: str, what: str) -> str:
    p = git(repo, "merge-base", a, b, allow_fail=True)
    if p.returncode != 0 or not p.stdout.strip():
        die(f"no common ancestor for {what} ({a[:12]}, {b[:12]}); cannot form a "
            f"baseline: {p.stderr.decode('utf-8', 'replace').strip()}")
    return out(p).strip()


def trusted_main_sha(repo: str, args) -> str:
    if args.main_ref.startswith("-") or args.fetch_remote.startswith("-"):
        die("--main-ref/--fetch-remote must be plain ref/remote names, not options")
    if args.main_sha:
        sha = args.main_sha.lower()
        if not SHA_RE.match(sha):
            die(f"--main-sha must be a full 40-hex SHA, got {args.main_sha!r}")
        ensure_commit(repo, sha, args.fetch_remote)
        return sha
    p = git(repo, "rev-parse", "--verify", "--quiet", f"{args.main_ref}^{{commit}}",
            allow_fail=True)
    if p.returncode != 0 or not p.stdout.strip():
        die(f"push with an all-zero 'before' on a non-main ref needs the protected "
            f"main as baseline, but {args.main_ref!r} does not resolve to a commit "
            f"here; provide --main-ref/--main-sha or fetch it")
    return out(p).strip()


def resolve_range(repo: str, event: dict, args) -> tuple[str, str, list[str]]:
    kind = event.get("event")
    remote = args.fetch_remote
    if kind == "pull_request":
        base = sha_field(event, "base")
        head = sha_field(event, "head")
        if base == ZERO_SHA:
            die("pull_request event has an all-zero base SHA")
        if head == ZERO_SHA:
            die("pull_request event has an all-zero head SHA")
        ensure_commit(repo, base, remote)
        ensure_commit(repo, head, remote)
        mb = merge_base(repo, base, head, "pull_request base/head")
        note = [
            f"event: pull_request base={base[:12]} head={head[:12]}",
            f"range: merge-base {mb[:12]}..head {head[:12]} "
            "(the synthetic merge checkout is never the checked head)",
        ]
        return mb, head, note
    if kind == "push":
        before = sha_field(event, "before")
        after = sha_field(event, "after")
        ref = event.get("ref", "")
        if not isinstance(ref, str):
            die("event field 'ref' must be a string")
        if after == ZERO_SHA:
            print("changed-line clang-format gate: SKIP "
                  "(push deletes the ref; after is all-zero)")
            raise SystemExit(0)
        ensure_commit(repo, after, remote)
        if before == ZERO_SHA:
            if ref in (args.main_name, f"refs/heads/{args.main_name}", ""):
                base = empty_tree(repo)
                note = [
                    f"event: push ref={ref or '<unspecified>'} before={ZERO_SHA[:12]} "
                    f"after={after[:12]}",
                    f"baseline: EMPTY TREE -> after (first push of {args.main_name}); "
                    "every existing line counts as added",
                ]
                return base, after, note
            main_sha = trusted_main_sha(repo, args)
            mb = merge_base(repo, main_sha, after, "protected main / after")
            note = [
                f"event: push ref={ref or '<unspecified>'} before={ZERO_SHA[:12]} "
                f"after={after[:12]}",
                f"baseline policy: branch increment relative to {args.main_ref} @ "
                f"{main_sha[:12]} (the event 'before' was all-zero; this is a "
                "declared fallback, not a historical before)",
                f"range: merge-base {mb[:12]}..after {after[:12]}",
            ]
            return mb, after, note
        ensure_commit(repo, before, remote)
        note = [
            f"event: push ref={ref or '<unspecified>'} before={before[:12]} "
            f"after={after[:12]}",
            f"range: before..after directly (batch commits, merges and force "
            "pushes; no merge-base, no runtime origin/main)",
        ]
        return before, after, note
    die(f"unsupported event type {kind!r}; expected 'pull_request' or 'push'")


def parse_global_diff(raw: bytes):
    """Return [(status, old_path, new_path, dst_oid)], patch_bytes.

    The raw section is NUL-framed (``-z``); the patch section follows it
    in the same traversal order, so patch chunk i belongs to record i.
    """
    records = []
    pos, n = 0, len(raw)
    while pos < n and raw[pos:pos + 1] == b":":
        nul1 = raw.find(b"\0", pos)
        nul2 = raw.find(b"\0", nul1 + 1)
        if nul1 < 0 or nul2 < 0:
            die("internal: truncated NUL-framed raw diff record")
        meta = raw[pos + 1:nul1].decode("utf-8", "surrogateescape")
        path1 = raw[nul1 + 1:nul2].decode("utf-8", "surrogateescape")
        parts = meta.split()
        if len(parts) != 5:
            die(f"internal: unexpected raw diff metadata {meta!r}")
        status = parts[4]
        if status.startswith(("R", "C")):
            nul3 = raw.find(b"\0", nul2 + 1)
            if nul3 < 0:
                die("internal: truncated rename record")
            path2 = raw[nul2 + 1:nul3].decode("utf-8", "surrogateescape")
            pos = nul3 + 1
        else:
            path2, pos = path1, nul2 + 1
        records.append((status, path1, path2, parts[3]))
    patch = raw[pos:]
    chunks = [c for c in re.split(rb"(?m)^diff --git ", patch) if c.strip()]
    if len(chunks) != len(records):
        die(f"internal: raw diff produced {len(records)} record(s) but "
            f"{len(chunks)} patch chunk(s); refusing to attribute hunks")
    return records, chunks


def safe_repo_path(path: str) -> None:
    if path.startswith(("/", "\\")):
        die(f"path {path!r} is absolute; repository paths must stay inside the root")
    for part in path.split("/"):
        if part in ("", ".", ".."):
            die(f"path {path!r} contains an empty/dot component; refusing paths "
                "that could escape the repository root")


def sanitize_component(name: str) -> str:
    """Map a repo path component to a filesystem-safe mirror component.

    Only the temp mirror (config snapshot + --assume-filename) is built
    from this; repository bytes are read by object ID and never touched.
    """
    cleaned = "".join(
        "_" if (ch in '<>:"|?*' or ord(ch) < 0x20 or ch in "\\/") else ch
        for ch in name
    ).rstrip(" .")
    return cleaned or "_"


def stage_config_snapshot(repo: str, head: str, temp_root: str) -> None:
    """Mirror the exact head tree's .clang-format files into temp_root."""
    p = git(repo, "ls-tree", "-r", "-z", head)
    staged_any = False
    for rec in p.stdout.split(b"\0"):
        if not rec:
            continue
        meta, name = rec.split(b"\t", 1)
        name_s = name.decode("utf-8", "surrogateescape")
        if name_s != ".clang-format" and not name_s.endswith("/.clang-format"):
            continue
        fields = meta.decode().split()
        blob = git(repo, "cat-file", "blob", fields[2]).stdout
        dest = os.path.join(temp_root,
                            *[sanitize_component(c) for c in name_s.split("/")])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
        staged_any = True
    if not staged_any:
        print("note: no .clang-format in the checked head tree; "
              "clang-format falls back exactly as it would in the repository")


def lf_line_starts(data: bytes):
    starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:
            starts.append(i + 1)
    total = len(starts) - (1 if data.endswith(b"\n") else 0)
    if not data:
        return [], 0
    return starts, max(total, 1)


def line_of(starts, total: int, offset: int) -> int:
    if not starts:
        return 1
    idx = bisect_right(starts, min(offset, starts[-1])) - 1
    return min(idx, total - 1) + 1


def check_file(cf: str, blob: bytes, assume_abs: str, path: str):
    p = run([cf, "--style=file", f"--assume-filename={assume_abs}",
             "--output-replacements-xml"], input_bytes=blob)
    if p.returncode != 0:
        die(f"clang-format failed on {path} (rc={p.returncode}): "
            f"{p.stderr.decode('utf-8', 'replace').strip()}")
    try:
        return ET.fromstring(p.stdout)
    except ET.ParseError as exc:
        die(f"cannot parse clang-format replacements XML for {path}: {exc}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="surrogateescape",
                               newline="\n")
        except (AttributeError, ValueError, OSError):
            pass

    ap = argparse.ArgumentParser(
        description="clang-format gate over changed lines only (CL-11 item 4)")
    ap.add_argument("--event-file", required=True, metavar="PATH|-",
                    help="event JSON file, or '-' for stdin")
    ap.add_argument("--repo", default=".", help="repository root (default: cwd)")
    ap.add_argument("--clang-format", default=f"clang-format-{MAJOR}",
                    help=f"clang-format-{MAJOR} binary (default: from PATH)")
    ap.add_argument("--fetch-remote", default="origin",
                    help="remote used to fetch missing exact event objects")
    ap.add_argument("--main-ref", default="origin/main",
                    help="protected main ref for the new-branch baseline policy")
    ap.add_argument("--main-sha", default=None,
                    help="explicit protected main SHA (overrides --main-ref)")
    ap.add_argument("--main-name", default="main",
                    help="protected main branch name (default: main)")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)

    git(repo, "rev-parse", "--git-dir")  # not a repository -> exit 2
    version_line = verify_formatter_major(args.clang_format)
    event = load_event(args.event_file)
    base, head, note = resolve_range(repo, event, args)

    raw = git(
        repo, "diff", "--raw", "--patch", "-z", "-U0", "--no-abbrev",
        "--find-renames", "--no-ext-diff", "--no-textconv", "--no-color",
        base, head, "--",
    ).stdout
    records, chunks = parse_global_diff(raw)

    candidates = []
    for (status, _old, new, _oid), chunk in zip(records, chunks):
        if status == "D" or new.startswith("validation/"):
            continue
        safe_repo_path(new)
        if os.path.splitext(new)[1].lower() not in CXX_EXTS:
            continue
        added = set()
        for m in HUNK_RE.finditer(chunk):
            start, count = int(m.group(2)), int(m.group(3) or "1")
            if count:
                added.update(range(start, start + count))
        if added:
            candidates.append((new, _oid, added))

    checked_lines = sum(len(a) for _, _, a in candidates)
    for line in note:
        print(line)
    print(f"formatter: {version_line}")

    violations = []
    if candidates:
        temp_root = tempfile.mkdtemp(prefix="check-changed-format-")
        try:
            stage_config_snapshot(repo, head, temp_root)
            for path, oid, added in sorted(candidates):
                safe_repo_path(path)
                blob = git(repo, "cat-file", "blob", oid).stdout
                assume = os.path.join(
                    temp_root, *[sanitize_component(c) for c in path.split("/")])
                os.makedirs(os.path.dirname(assume), exist_ok=True)
                root = check_file(args.clang_format, blob, assume, path)
                if root.get("incomplete_format") == "true":
                    die(f"clang-format could only incompletely format {path} "
                        f"(stopped at line {root.get('line')}); incomplete "
                        "formatting is a tooling failure, never a silent pass")
                starts, total = lf_line_starts(blob)
                for rep in root.iter("replacement"):
                    offset, length = int(rep.get("offset")), int(rep.get("length"))
                    lo = line_of(starts, total, offset)
                    hi = line_of(starts, total, offset + max(length - 1, 0))
                    hit = sorted(added & set(range(lo, hi + 1)))
                    if hit:
                        snippet = blob[offset:offset + length][:100]
                        violations.append(
                            (path, hit[0],
                             snippet.decode("utf-8", "surrogateescape"),
                             (rep.text or "")[:100]))
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    if violations:
        violations.sort()
        print("changed-line clang-format gate: FAIL")
        print(f"{len(candidates)} changed C/C++ file(s), "
              f"{checked_lines} added line(s) checked")
        for path, line, old, new in violations:
            print(f"\n{path}:{line}: changed line(s) not clang-format clean")
            print(f"  replace {old!r} with {new!r}")
        bad = sorted({p for p, _, _, _ in violations})
        print(f"\n{len(bad)} file(s), {len(violations)} replacement(s) violate "
              ".clang-format on added lines.")
        print("Reformat the changed regions only; do not reformat untouched lines.")
        return 1

    print("changed-line clang-format gate: PASS "
          f"({len(candidates)} changed C/C++ file(s), "
          f"{checked_lines} added line(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
