#!/usr/bin/env python3
"""Read-only structural check of the authoritative reviewed v1.2 plan.

Usage:
    python3 docs/planning/tools/plan_v1_2.py check
    python3 docs/planning/tools/plan_v1_2.py check --plan COPY --backlog BASELINE

No arguments also runs check. The former --write generator is deliberately
blocked: its tables cannot reconstruct reviewed acceptance mappings, deferred
semantics or approval states. Edit the authoritative JSON/blueprint explicitly
and review the diff; this tool never generates, normalizes or saves them.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def read_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=unique_object, parse_constant=invalid_constant)
    require(isinstance(data, dict), f"{path}: expected a JSON object")
    return data


def index_rows(value: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    require(isinstance(value, list) and bool(value), f"{label}: nonempty array required")
    indexed: dict[str, dict[str, Any]] = {}
    for row in value:
        require(isinstance(row, dict), f"{label}: object entries required")
        ident = row.get(key)
        require(isinstance(ident, str) and bool(ident), f"{label}: nonempty {key} required")
        require(ident not in indexed, f"{label}: duplicate {key} {ident}")
        indexed[ident] = row
    return indexed


def string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    require(isinstance(value, list), f"{label}: array required")
    require(all(isinstance(item, str) and bool(item) for item in value),
            f"{label}: nonempty strings required")
    require(len(value) == len(set(value)), f"{label}: duplicates are not allowed")
    require(not nonempty or bool(value), f"{label}: must not be empty")
    return value


def same_ids(actual: dict[str, Any], expected: dict[str, Any], label: str) -> None:
    missing, extra = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
    require(not missing and not extra, f"{label}: missing={missing}; extra={extra}")


def natural(value: Any, label: str) -> int:
    require(type(value) is int and value >= 0, f"{label}: nonnegative integer required")
    return value


def validate(plan: dict[str, Any], backlog: dict[str, Any]) -> dict[str, Any]:
    baseline = index_rows(backlog.get("features"), "id", "backlog.features")
    baseline_cases = index_rows(backlog.get("acceptance"), "id", "backlog.acceptance")
    packages = index_rows(plan.get("packages"), "id", "plan.packages")
    slices = index_rows(plan.get("slices"), "slice", "plan.slices")
    mappings = index_rows(plan.get("acceptance_mapping"), "id", "plan.acceptance_mapping")
    same_ids(packages, baseline, "package IDs")
    same_ids(mappings, baseline_cases, "acceptance IDs")
    require(isinstance(plan.get("review_revision"), str) and bool(plan["review_revision"]),
            "review_revision is required; legacy generated plans are not reviewed plans")
    policy = plan.get("scope_policy")
    require(isinstance(policy, dict), "scope_policy review state is required")

    order = {sid: pos for pos, sid in enumerate(slices)}
    counts: Counter[str] = Counter()
    effort = {sid: [0, 0] for sid in slices}
    graph: dict[str, list[str]] = {}
    accepted = 0
    for fid, package in packages.items():
        sid = package.get("slice")
        require(isinstance(sid, str) and sid in slices, f"{fid}: unknown slice {sid}")
        dependencies = string_list(package.get("depends"), f"{fid}.depends")
        for dep in dependencies:
            require(dep in packages, f"{fid}: unknown dependency {dep}")
        graph[fid] = dependencies
        counts[sid] += 1
        lo = natural(package.get("effort_low"), f"{fid}.effort_low")
        hi = natural(package.get("effort_high"), f"{fid}.effort_high")
        require(lo <= hi, f"{fid}: effort_low exceeds effort_high")
        effort[sid][0] += lo
        effort[sid][1] += hi
        require(type(package.get("full_work_package_accepted")) is bool,
                f"{fid}: explicit full_work_package_accepted state required")
        accepted += int(package["full_work_package_accepted"])

    for fid, dependencies in graph.items():
        for dep in dependencies:
            require(order[packages[dep]["slice"]] <= order[packages[fid]["slice"]],
                    f"{fid}: depends on later slice via {dep}")

    # Iterative traversal avoids recursion limits and detects same-slice cycles.
    degree = {fid: len(deps) for fid, deps in graph.items()}
    children: dict[str, list[str]] = {fid: [] for fid in graph}
    for fid, dependencies in graph.items():
        for dep in dependencies:
            children[dep].append(fid)
    ready = [fid for fid, n in degree.items() if n == 0]
    visited = 0
    while ready:
        fid = ready.pop()
        visited += 1
        for child in children[fid]:
            degree[child] -= 1
            if degree[child] == 0:
                ready.append(child)
    require(visited == len(graph), "package dependencies contain a cycle")

    for sid, row in slices.items():
        require(natural(row.get("count"), f"{sid}.count") == counts[sid], f"{sid}: count mismatch")
        for pos, field in enumerate(("effort_low", "effort_high")):
            require(natural(row.get(field), f"{sid}.{field}") == effort[sid][pos],
                    f"{sid}: {field} total mismatch")
    totals = [sum(item[pos] for item in effort.values()) for pos in (0, 1)]
    require(isinstance(plan.get("total_effort"), list) and len(plan["total_effort"]) == 2,
            "total_effort must contain low and high")
    for pos, expected in enumerate(totals):
        require(natural(plan["total_effort"][pos], "total_effort") == expected,
                "total_effort mismatch")
    require(natural(policy.get("full_work_packages_accepted"), "scope_policy.full_work_packages_accepted")
            == accepted, "accepted package count does not match per-package review states")
    approved_slices = string_list(policy.get("slices_accepted"), "scope_policy.slices_accepted")
    require(all(sid in slices for sid in approved_slices), "accepted slice is unknown")
    require(type(policy.get("planned_acceptance_suite_executed")) is bool,
            "scope_policy.planned_acceptance_suite_executed must be explicit")

    for ident, mapping in mappings.items():
        require(mapping.get("baseline_acceptance") == baseline_cases[ident].get("acceptance"),
                f"{ident}: baseline acceptance text differs; preserve the original requirement")
        fids = string_list(mapping.get("feature_ids"), f"{ident}.feature_ids")
        require(all(fid in packages for fid in fids), f"{ident}: unknown mapped feature")
        sids = string_list(mapping.get("planned_slices"), f"{ident}.planned_slices", nonempty=True)
        require(all(sid in slices for sid in sids), f"{ident}: unknown mapped slice")
        for field in ("equivalent_requirement", "execution_status"):
            require(isinstance(mapping.get(field), str) and bool(mapping[field]),
                    f"{ident}: {field} is required")
        require(isinstance(mapping.get("evidence"), list), f"{ident}: evidence array is required")

    return {"read_only": True, "structure_valid": True, "packages": len(packages),
            "acceptance_mappings": len(mappings), "slices": len(slices),
            "planning_estimate": totals, "product_acceptance_checked": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("check",), default="check")
    parser.add_argument("--plan", type=Path, default=ROOT / "slices_v1.2.json")
    parser.add_argument("--backlog", type=Path, default=ROOT / "requirements_and_backlog.json")
    parser.add_argument("--write", action="store_true", help="disabled: reviewed documents must not be regenerated")
    args = parser.parse_args(argv)
    if args.write:
        parser.error("--write is disabled: legacy generation would discard reviewed mappings/states. "
                     "Edit the authoritative JSON/blueprint explicitly and run the read-only 'check' command.")
    try:
        summary = validate(read_object(args.plan), read_object(args.backlog))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"plan check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
