#!/usr/bin/env python3
"""GOV-01 offline SBOM + license evidence generator (Python stdlib only).

Implements node 3 of docs/planning/THREE_NODES_20261029.md, including review
amendment #10: the lock file MEASURES 60 pinned packages (62 total lines =
60 pins + 2 comments; the earlier "58" figure was wrong), and the vcpkg
installation tree MEASURES 67 vcpkg.spdx.json documents. Both counts are
asserted at runtime against what this script actually measures.

Read-only by design: binds the report to `git rev-parse HEAD`, compares the
working lock file against HEAD, and never writes outside --out. No network
access, no third-party imports.

Each row: name, kind ("python"|"vcpkg-port"), version, license,
license_evidence (which file and which field the license came from),
replacement_strategy (null unless documented repo evidence exists - do not
invent; see REPLACEMENT_EVIDENCE).

Determinism: rows sorted, JSON dumped with sort_keys=True; two runs are
byte-identical except the single top-level "generated_at" timestamp.

Usage:
  C:/Python312/python.exe tools/sbom_report.py
  C:/Python312/python.exe tools/sbom_report.py --out validation/sbom_20261029/sbom-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Review amendment #10 (THREE_NODES_20261029.md line 23-24): GOV acceptance
# counts are the MEASURED 60 pinned python packages and 67 vcpkg SPDX docs.
EXPECT_PYTHON = 60
EXPECT_VCPKG = 67

VCPKG_BASELINE_DEFAULT = "6719fd4d4cd3b2d4575f6164da4a02f757b3b067"  # pinned in .github/workflows/ci.yml (ref:)

# SPDX ids that mean "no license conclusion available". vcpkg emits
# LicenseRef-vcpkg-null when its own port metadata carries no license.
NULL_LICENSE_VALUES = {"NOASSERTION", "NONE", "LicenseRef-vcpkg-null"}

# Curated subset of the SPDX license-list ids covering everything measured in
# this repo plus the common permissive/copyleft ids nearby. NOT exhaustive:
# an unknown token classifies the value as non_spdx_text (conservative; the
# raw value is always preserved in the row).
CURATED_SPDX_IDS = {
    "0BSD", "Apache-1.0", "Apache-1.1", "Apache-2.0", "Artistic-2.0",
    "BSD-2-Clause", "BSD-2-Clause-Patent", "BSD-3-Clause", "BSD-3-Clause-Clear",
    "BSL-1.0", "blessing", "CC0-1.0", "CNRI-Python", "curl", "EPL-1.0",
    "EPL-2.0", "EUPL-1.2", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "ISC", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only",
    "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT", "MIT-0",
    "MPL-1.1", "MPL-2.0", "MS-PL", "NCSA", "OpenSSL", "PSF-2.0",
    "Python-2.0", "Sleepycat", "Unlicense", "Zlib", "ZPL-2.1",
}
CURATED_SPDX_EXCEPTIONS = {"LLVM-exception", "Classpath-exception-2.0", "Autoconf-exception-3.0"}

# Copyleft tiers for anomaly flags (task: tld MPL/GPL -> strong-copyleft flag).
STRONG_COPYLEFT_PREFIXES = ("GPL-", "LGPL-", "AGPL-")
WEAK_COPYLEFT_PREFIXES = ("MPL-", "EPL-", "CDDL-")

# Provenance: docs/DEPENDENCIES.md table "当前依赖范围" transcribed VERBATIM as
# of git commit 146138773eb3ad114a5c2b8a8c479b31e24aa98c (HEAD at authoring,
# 2026-10-29, branch work/blueprint-v2-dev-20261029). These labels are frozen
# diff anchors; when the doc table changes, regenerate this provenance note.
HAND_TABLE = [
    ("SQLite（含 FTS5）", "Public Domain", ("sqlite3",)),
    ("Boost.Asio / Beast", "BSL-1.0", ("boost-asio", "boost-beast")),
    ("json-c", "MIT", ("json-c",)),
    ("libxml2", "MIT", ("libxml2",)),
    ("OpenSSL libcrypto", "Apache-2.0", ("openssl",)),
    ("aiohttp", "Apache-2.0 / MIT，按发行物核对", ("aiohttp",)),
    ("lxml / cssselect", "BSD-3-Clause", ("lxml", "cssselect")),
    ("trafilatura", "Apache-2.0", ("trafilatura",)),
    ("MCP Python SDK", "MIT", ("mcp", "mcp-types")),
    ("pytest / pytest-asyncio", "MIT / Apache-2.0，按发行物核对", ("pytest", "pytest-asyncio")),
]

# Provenance: replacement strategies may only cite repo evidence. A survey of
# docs/*.md at commit 1461387 (searched 替换/替代/候选 and package names) found
# no per-package replacement mapping, so this table is empty and every row
# carries replacement_strategy=null. "后续候选" in DEPENDENCIES.md lists future
# additions (Playwright, PDF/Office parsers, OpenCC, sqlite-vec), not
# replacements. Keep this table empty unless a doc actually records one.
REPLACEMENT_EVIDENCE: dict = {}

# The audit-dependencies workflow only downloads Linux cp313 wheels and hashes
# THOSE bytes; this script runs offline and cannot reproduce wheel hashes.
# As a clearly-labeled substitute it hashes the lock file and each dist-info
# METADATA file. This is NOT a hash of the installed wheels.
BINDING_NOTE = (
    "NOT wheel hashes: audit-dependencies.yml (wheels job) hashes downloaded "
    "cp313 wheel bytes in CI; this offline report instead hashes the lock file "
    "and each python dist-info METADATA file, plus each vcpkg.spdx.json. "
    "Those hashes bind local metadata text, not wheel/distribution bytes."
)


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(args: list, cwd: Path = REPO_ROOT, binary: bool = False):
    res = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.decode('utf-8', 'replace').strip()}")
    return res.stdout if binary else res.stdout.decode("utf-8").strip()


def parse_lock(path: Path) -> list:
    pkgs = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if "==" not in line:
            raise ValueError(f"{path}:{lineno}: non-comment line without '==': {line!r}")
        name, _, version = line.partition("==")
        pkgs.append((name.strip(), version.strip()))
    return pkgs


def site_packages_dirs(venv: Path) -> list:
    dirs = []
    win = venv / "Lib" / "site-packages"
    if win.is_dir():
        dirs.append(win)
    dirs.extend(sorted(p for p in (venv / "lib").glob("python*/site-packages") if p.is_dir()))
    if not dirs:
        raise FileNotFoundError(f"no site-packages under {venv}")
    return dirs


def find_dist_info(venv: Path, name: str, version: str):
    for sp in site_packages_dirs(venv):
        for d in sp.glob("*.dist-info"):
            stem = d.name[: -len(".dist-info")]
            dn, _, dv = stem.partition("-")
            if dv == version and norm_name(dn) == norm_name(name):
                return d
    return None


def metadata_license_field(dist_info: Path):
    """License-Expression first, then License, then License:: classifier.

    Returns (field, value) where field records WHICH metadata field was used,
    or ("none", None) when no license field exists at all.
    """
    text = dist_info.joinpath("METADATA").read_text(encoding="utf-8", errors="replace")
    header_block = text.split("\n\n", 1)[0]
    msg = Parser().parsestr(header_block)
    value = msg.get("License-Expression")
    if value and value.strip():
        return "License-Expression", re.sub(r"\s+", " ", value).strip()
    value = msg.get("License")
    if value and value.strip():
        return "License", re.sub(r"\s+", " ", value).strip()
    cls = [v.strip() for k, v in msg.items() if k == "Classifier" and v.strip().startswith("License ::")]
    if cls:
        # Multiple classifiers joined with " ; " - evidence listing, not a
        # synthesized SPDX expression.
        return "Classifier:License", " ; ".join(re.sub(r"\s+", " ", c) for c in cls)
    return "none", None


def parse_spdx_expression(value: str):
    """Validate our curated flat SPDX subset, without discarding input bytes.

    This is not a complete SPDX parser: grouped expressions, LicenseRef values
    and unlisted identifiers remain unclassified raw evidence. WITH must name
    a known exception, not a second license. IDs are case-insensitive; operators
    are case-sensitive (SPDX 2.x Annex D). Never infer permissions from syntax.
    """
    invalid = (False, [], False)
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return invalid
    tokens = value.split()
    if len(tokens) > 256:
        return invalid
    licenses = {item.casefold(): item for item in CURATED_SPDX_IDS}
    exceptions = {item.casefold() for item in CURATED_SPDX_EXCEPTIONS}
    ids, compound, pos = [], False, 0
    while pos < len(tokens):
        # Exact token lookup: punctuation, partial IDs and unknown text cannot
        # be silently erased by regex token extraction and become valid SPDX.
        license_id = licenses.get(tokens[pos].casefold())
        if license_id is None:
            return invalid
        ids.append(license_id)
        pos += 1
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            if pos >= len(tokens) or tokens[pos].casefold() not in exceptions:
                return invalid
            pos += 1
        if pos == len(tokens):
            return True, ids, compound
        if tokens[pos] not in ("AND", "OR"):
            return invalid
        compound = True
        pos += 1
    # Ending at an operator is not a complete expression.
    return invalid


def classify_license(value):
    """Return (classification, flags) for a raw license value."""
    if value is None or value in NULL_LICENSE_VALUES:
        return "no-license", set()
    ok, ids, compound = parse_spdx_expression(value)
    if not ok:
        return "non-spdx-text", set()
    flags = set()
    if compound:
        flags.add("compound")
    strong = [i for i in ids if i.startswith(STRONG_COPYLEFT_PREFIXES)]
    weak = [i for i in ids if i.startswith(WEAK_COPYLEFT_PREFIXES)]
    if strong:
        flags.add("strong-copyleft")
    if weak:
        flags.add("weak-copyleft")
    return "spdx", flags


def copyright_text_head(copyright_path: Path, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", copyright_path.read_text(encoding="utf-8", errors="replace")).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def load_vcpkg_spdx(share: Path) -> dict:
    docs = {}
    for f in sorted(share.glob("*/vcpkg.spdx.json")):
        port = f.parent.name
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        pkg = next((p for p in data.get("packages", []) if p.get("SPDXID") == "SPDXRef-port"), None)
        if pkg is None:
            pkg = data.get("packages", [{}])[0]
        docs[port] = {
            "version": pkg.get("versionInfo"),
            "license": pkg.get("licenseConcluded"),
            "spdx_file": f,
        }
    return docs


def installed_ports(share: Path):
    """Unique installed port names from installed/vcpkg/status, or None."""
    status = share.parent.parent / "vcpkg" / "status"
    if not status.is_file():
        return None
    names = set()
    for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Package:"):
            names.add(line.partition(":")[2].strip())
    return sorted(names)


def build_python_rows(lock_path: Path, venv: Path, anomalies: list):
    rows = []
    meta_hash_lines = []
    missing = []
    for name, version in parse_lock(lock_path):
        dist = find_dist_info(venv, name, version)
        if dist is None:
            missing.append(f"{name}=={version}")
            rows.append({
                "name": name, "kind": "python", "version": version,
                "license": "NOASSERTION",
                "license_evidence": "missing-dist-info",
                "replacement_strategy": REPLACEMENT_EVIDENCE.get(norm_name(name)),
            })
            anomalies.append({"kind": "no-license", "name": f"{name}=={version}",
                              "detail": "no dist-info in venv for locked package"})
            continue
        field, value = metadata_license_field(dist)
        meta_hash_lines.append(f"{norm_name(name)}=={version} {sha256_file(dist / 'METADATA')}")
        if field == "none":
            license_value = "NOASSERTION"
            evidence = f"dist-info:{dist.name}/METADATA#no-license-field"
        elif value in NULL_LICENSE_VALUES:
            license_value = "NOASSERTION"
            evidence = f"dist-info:{dist.name}/METADATA#{field}={value}"
        else:
            license_value = value
            evidence = f"dist-info:{dist.name}/METADATA#{field}"
        rows.append({
            "name": name, "kind": "python", "version": version,
            "license": license_value,
            "license_evidence": evidence,
            "replacement_strategy": REPLACEMENT_EVIDENCE.get(norm_name(name)),
        })
        classification, flags = classify_license(value)
        if classification == "no-license":
            anomalies.append({"kind": "no-license", "name": name,
                              "detail": f"METADATA carries no usable license field"})
        elif classification == "non-spdx-text":
            detail = f"METADATA {field} is free text, not an SPDX expression"
            if field == "License":
                cls = [v.strip() for k, v in Parser().parsestr(
                    (dist / "METADATA").read_text(encoding="utf-8", errors="replace").split("\n\n", 1)[0]
                ).items() if k == "Classifier" and v.strip().startswith("License ::")]
                if cls:
                    detail += f"; classifiers suggest: {' ; '.join(cls)}"
            anomalies.append({"kind": "non-spdx-text", "name": name, "detail": detail})
        for flag in sorted(flags):
            anomalies.append({"kind": flag, "name": name, "detail": f"{field}: {value}"})
    aggregate = hashlib.sha256("\n".join(sorted(meta_hash_lines)).encode("utf-8")).hexdigest()
    return rows, missing, aggregate, len(meta_hash_lines)


def build_vcpkg_rows(share: Path, anomalies: list):
    rows = []
    spdx = load_vcpkg_spdx(share)
    installed = installed_ports(share)
    spdx_hash_lines = [
        f"{port} {sha256_file(info['spdx_file'])}" for port, info in sorted(spdx.items())
    ]
    aggregate = hashlib.sha256("\n".join(sorted(spdx_hash_lines)).encode("utf-8")).hexdigest()
    ports_root = share.parent.parent.parent / "ports"
    no_spdx_ports = []
    for port in sorted(set(spdx) | set(installed or [])):
        info = spdx.get(port)
        if info is not None:
            raw = info["license"]
            if raw in NULL_LICENSE_VALUES:
                license_value = "NOASSERTION"
                evidence = f"vcpkg-spdx:share/{port}/vcpkg.spdx.json#licenseConcluded={raw}"
                detail = f"SPDX licenseConcluded is {raw} (no license conclusion)"
                cp = ports_root / port / "copyright"
                if cp.is_file():
                    detail += f"; ports/{port}/copyright head: {copyright_text_head(cp)}"
                anomalies.append({"kind": "no-license", "name": port, "detail": detail})
            else:
                license_value = raw
                evidence = f"vcpkg-spdx:share/{port}/vcpkg.spdx.json#SPDXRef-port.licenseConcluded"
            version = info["version"]
        else:
            no_spdx_ports.append(port)
            cp = ports_root / port / "copyright"
            if cp.is_file():
                license_value = copyright_text_head(cp)
                evidence = f"vcpkg-copyright:ports/{port}/copyright"
                anomalies.append({"kind": "non-spdx-text", "name": port,
                                  "detail": f"no vcpkg.spdx.json; license from ports/{port}/copyright free text"})
            else:
                license_value = "NOASSERTION"
                evidence = f"vcpkg:none:share/{port} has neither vcpkg.spdx.json nor ports/{port}/copyright"
                anomalies.append({"kind": "no-license", "name": port,
                                  "detail": "no vcpkg.spdx.json and no ports/<port>/copyright -> NOASSERTION"})
            version = None
            try:
                vcpkg_json = ports_root / port / "vcpkg.json"
                if vcpkg_json.is_file():
                    version = json.loads(vcpkg_json.read_text(encoding="utf-8-sig")).get("version")
            except (OSError, ValueError):
                pass
        rows.append({
            "name": port, "kind": "vcpkg-port", "version": version,
            "license": license_value,
            "license_evidence": evidence,
            "replacement_strategy": REPLACEMENT_EVIDENCE.get(norm_name(port)),
        })
        if info is not None and raw not in NULL_LICENSE_VALUES:
            classification, flags = classify_license(raw)
            if classification == "non-spdx-text":
                anomalies.append({"kind": "non-spdx-text", "name": port,
                                  "detail": f"licenseConcluded is not a curated SPDX expression: {raw}"})
            for flag in sorted(flags):
                anomalies.append({"kind": flag, "name": port,
                                  "detail": f"SPDXRef-port.licenseConcluded: {raw}"})
    return rows, len(spdx), installed, no_spdx_ports, aggregate


def build_diff(rows: list) -> dict:
    by_name = {(r["name"].lower()): r for r in rows if r["kind"] == "python"}
    by_name.update({r["name"]: r for r in rows if r["kind"] == "vcpkg-port"})
    covered = set()
    out_rows = []
    for component, label, names in HAND_TABLE:
        measured = {}
        for n in names:
            row = by_name.get(n.lower())
            if row is not None:
                measured[n] = row["license"]
                covered.add(n.lower())
        annotation = "，按发行物核对" in label
        base = label.replace("，按发行物核对", "")
        parts = [p.strip() for p in base.split("/")] if " / " in base else [base.strip()]
        if not measured:
            status = "not-measured"
        elif len(parts) > 1 and len(measured) == 1:
            # aiohttp: hand slash ambiguous over one package; measured fixes
            # the operator (AND). Both sides present -> refined, not mismatch.
            license_ids = set()
            ok, ids, _ = parse_spdx_expression(next(iter(measured.values())))
            if ok and all(p in ids for p in parts):
                status = "refined"
            else:
                status = "mismatch"
        else:
            values = list(measured.values())
            hand_vals = parts if len(parts) == len(values) else parts * len(values)
            equal = sorted(hand_vals) == sorted(values)
            status = ("refined" if equal and annotation else "match" if equal else "mismatch")
        out_rows.append({
            "component": component, "hand_label": label,
            "measured": measured, "status": status,
        })
    absent = sorted(n for n, r in by_name.items() if n not in covered)
    return {
        "provenance": (
            "hand labels transcribed verbatim from docs/DEPENDENCIES.md table "
            "『当前依赖范围』 at git commit 146138773eb3ad114a5c2b8a8c479b31e24aa98c "
            "(tools/sbom_report.py HAND_TABLE); statuses: match=measured equals "
            "hand label, refined=hand placeholder/ambiguity resolved by measured "
            "evidence, mismatch=definite label differs, not-measured=no row"
        ),
        "rows": out_rows,
        "measured_not_covered_by_hand_table": absent,
        "coverage_note": (
            f"hand table names {len(covered)} of {len(by_name)} measured rows; it is "
            "component-level (one row for two boost ports, none for the other 53 "
            "boost ports, zlib, libiconv and the 5 vcpkg-* build-tool ports, and "
            "none for transitive python pins)"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline SBOM + license evidence report (GOV-01).")
    ap.add_argument("--lock", default="tools/requirements-s1-lock.txt")
    ap.add_argument("--venv", default="C:/bzs861-v2/.venv")
    ap.add_argument("--vcpkg-share", default="C:/tools/vcpkg-bzs/installed/x64-windows/share")
    ap.add_argument("--vcpkg-baseline", default=VCPKG_BASELINE_DEFAULT)
    ap.add_argument("--out", default=None,
                    help="default: <repo>/validation/sbom_20261029/sbom-report.json (repo-rooted)")
    ap.add_argument("--expect-python", type=int, default=EXPECT_PYTHON)
    ap.add_argument("--expect-vcpkg", type=int, default=EXPECT_VCPKG)
    args = ap.parse_args()

    lock = (REPO_ROOT / args.lock).resolve() if not Path(args.lock).is_absolute() else Path(args.lock)
    venv = Path(args.venv)
    share = Path(args.vcpkg_share)
    out = (REPO_ROOT / "validation/sbom_20261029/sbom-report.json") if args.out is None else Path(args.out)

    anomalies: list = []
    python_rows, missing_dist, meta_aggregate, meta_count = build_python_rows(lock, venv, anomalies)
    vcpkg_rows, spdx_count, installed, no_spdx_ports, spdx_aggregate = build_vcpkg_rows(share, anomalies)

    # Amendment #10: assert the MEASURED counts, not the superseded 58.
    assert len(python_rows) == args.expect_python, (
        f"measured {len(python_rows)} locked python packages, expected {args.expect_python} "
        f"(amendment #10: 60 pins; 62 = pins + 2 comment lines)")
    assert spdx_count == args.expect_vcpkg, (
        f"measured {spdx_count} vcpkg.spdx.json documents, expected {args.expect_vcpkg}")
    for row in python_rows + vcpkg_rows:
        assert set(row) == {"name", "kind", "version", "license", "license_evidence",
                            "replacement_strategy"}, f"row fields drifted: {row}"

    rows = sorted(python_rows + vcpkg_rows, key=lambda r: (r["kind"], r["name"].lower(), r["name"]))
    anomalies.sort(key=lambda a: (a["kind"], a["name"].lower()))

    # Read-only git binding (task: bind to HEAD via git rev-parse).
    git_commit = git(["rev-parse", "HEAD"])
    git_branch = git(["rev-parse", "--abbrev-ref", "HEAD"])
    lock_sha = sha256_file(lock)
    lock_head_sha = None
    try:
        rel = lock.resolve().relative_to(REPO_ROOT).as_posix()
        lock_head_sha = hashlib.sha256(git(["show", f"HEAD:{rel}"], binary=True)).hexdigest()
    except (ValueError, RuntimeError):
        pass
    ci_pin = None
    ci_path = REPO_ROOT / ".github/workflows/ci.yml"
    if ci_path.is_file():
        ci_pin = args.vcpkg_baseline in ci_path.read_text(encoding="utf-8", errors="replace")
    baseline_obj = "unavailable"
    try:
        baseline_obj = git(["cat-file", "-t", args.vcpkg_baseline], cwd=share.parent.parent.parent)
    except RuntimeError:
        pass

    by_kind = {}
    for a in anomalies:
        by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1
    report = {
        "schema": "bzs-sbom-report/1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {
            "git_commit": git_commit,
            "git_branch": git_branch,
            "tool": "tools/sbom_report.py",
            "tool_sha256": sha256_file(Path(__file__).resolve()),
            "lock_file": str(lock),
            "lock_sha256": lock_sha,
            "lock_sha256_at_head": lock_head_sha,
            "lock_unchanged_vs_head": (lock_sha == lock_head_sha) if lock_head_sha else None,
            "vcpkg_baseline": args.vcpkg_baseline,
            "vcpkg_baseline_in_ci_yml": ci_pin,
            "vcpkg_baseline_git_object": baseline_obj,
            "vcpkg_share": str(share),
            "metadatas_aggregate_sha256": meta_aggregate,
            "metadata_files_hashed": meta_count,
            "vcpkg_spdx_aggregate_sha256": spdx_aggregate,
            "local_binding_note": BINDING_NOTE,
        },
        "counts": {
            "python_packages": len(python_rows),
            "python_dist_info_missing": len(missing_dist),
            "vcpkg_spdx_docs": spdx_count,
            "vcpkg_installed_ports": len(installed) if installed is not None else None,
            "vcpkg_ports_without_spdx_on_this_machine": len(no_spdx_ports),
            "vcpkg_rows": len(vcpkg_rows),
            "total_rows": len(rows),
            "anomalies_by_kind": by_kind,
            "replacement_strategy_null": sum(1 for r in rows if r["replacement_strategy"] is None),
        },
        "rows": rows,
        "anomalies": anomalies,
        "diff_vs_hand_table": build_diff(rows),
        "scope_limits": [
            "row licenses are metadata text as installed (dist-info METADATA / vcpkg.spdx.json); LICENSE files themselves were not re-read or legally reviewed",
            "NOASSERTION rows are enumerated in anomalies; no license was inferred",
            "CI runner system packages remain covered only by the existing validation/s1_review/environment.json record (recorded, not re-reviewed)",
            "not a full-product or release SBOM acceptance; project license choice stays with the repository owner",
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"commit {git_commit} ({git_branch})")
    print(f"python rows: {len(python_rows)} (missing dist-info: {len(missing_dist)})")
    print(f"vcpkg rows: {len(vcpkg_rows)} (spdx docs: {spdx_count}, installed ports: {installed and len(installed)}, "
          f"no-spdx ports: {len(no_spdx_ports)})")
    print(f"anomalies: {len(anomalies)} {json.dumps(by_kind, sort_keys=True)}")
    diff_stat = {}
    for r in report["diff_vs_hand_table"]["rows"]:
        diff_stat[r["status"]] = diff_stat.get(r["status"], 0) + 1
    print(f"hand-table diff: {json.dumps(diff_stat, sort_keys=True)}; "
          f"not covered by hand table: {len(report['diff_vs_hand_table']['measured_not_covered_by_hand_table'])}")
    print(f"lock sha256 {lock_sha} (unchanged vs HEAD: {report['binding']['lock_unchanged_vs_head']})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
