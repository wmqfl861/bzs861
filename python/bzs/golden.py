"""Bounded golden-query loading and deterministic, capability-aware selection.

The checked-in 40-query dataset is frozen by its sibling manifest. Its manifest
hash permits LF/CRLF checkout conversion; ``digest`` always identifies the exact
input bytes. Legacy probe fixtures remain usable but are explicitly not frozen.
Neither loading nor selecting queries performs network access or judges relevance.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

MAX_INPUT_BYTES = 1024 * 1024
MAX_QUERIES = 1000
DATASET_ID = "bzs861-golden-40"
CAPABILITIES = frozenset({"web", "encyclopedia", "papers", "code", "news"})
CATEGORIES = frozenset({"exact_symbol", "zh_tech", "navigational", "academic", "code", "news",
                        "general_zh", "general_en", "holdout", "legacy"})
SPLITS = frozenset({"development", "regression", "holdout"})
QUERY_FIELDS = frozenset({"id", "query", "category", "language", "good_for", "split"})
CONTENT_FIELDS = ("id", "query", "category", "language", "good_for")


@dataclass(frozen=True)
class GoldenSet:
    queries: list[dict[str, Any]]
    digest: str
    version: int | None
    frozen: bool
    manifest_digest: str | None = None


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _read_object(path: Path) -> tuple[dict[str, Any], bytes]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    _need(len(raw) <= MAX_INPUT_BYTES, "golden input exceeds 1 MiB")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("golden input must be bounded UTF-8 JSON") from exc
    _need(isinstance(result, dict), "golden input must be a JSON object")
    return result, raw


def _text(value: Any, name: str, maximum: int) -> str:
    _need(isinstance(value, str) and 0 < len(value) <= maximum and bool(value.strip()),
          f"{name} must be a nonempty string of at most {maximum} characters")
    _need(not any(ord(ch) < 32 or ord(ch) == 127 for ch in value), f"{name} contains a control character")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{name} is not valid Unicode") from exc
    return value


def _normalize_queries(value: Any, *, frozen: bool = False) -> list[dict[str, Any]]:
    _need(isinstance(value, list) and 1 <= len(value) <= MAX_QUERIES,
          "queries must be an array with 1..1000 entries")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in value:
        _need(isinstance(row, dict), "each golden query must be an object")
        _need(not row.keys() - QUERY_FIELDS, "unknown golden query field")
        if frozen:
            _need(row.keys() == QUERY_FIELDS, "frozen queries require all six query metadata fields")
        ident = _text(row.get("id"), "query id", 80)
        _need(bool(re.fullmatch(r"[A-Za-z0-9_-]+", ident)), "query id must be a safe identifier")
        _need(ident not in seen, f"duplicate query id: {ident}")
        seen.add(ident)
        query = _text(row.get("query"), "query", 1000)
        _need(len(query.encode("utf-8")) <= 4096, "query exceeds 4096 UTF-8 bytes")
        good_for = row.get("good_for", ["web"])
        _need(isinstance(good_for, list) and 1 <= len(good_for) <= len(CAPABILITIES) and
              all(isinstance(item, str) and item in CAPABILITIES for item in good_for),
              "good_for must be a nonempty array of supported capabilities")
        _need(len(good_for) == len(set(good_for)), "duplicate capability in good_for")
        # The legacy notes reserve h* IDs for holdout queries. Explicit metadata
        # cannot silently turn such an ID back into a development query.
        reserved_holdout = ident.lower().startswith("h")
        category = row.get("category", "holdout" if reserved_holdout else "legacy")
        _need(isinstance(category, str) and category in CATEGORIES, "unsupported query category")
        split = row.get("split", "holdout" if reserved_holdout or category == "holdout" else "regression")
        _need(isinstance(split, str) and split in SPLITS, "unsupported query split")
        _need(not (reserved_holdout or category == "holdout") or split == "holdout",
              "holdout IDs/categories cannot be assigned to a non-holdout split")
        normalized = {"id": ident, "query": query, "good_for": list(good_for),
                      "category": category, "split": split}
        if "language" in row:
            language = row["language"]
            _need(isinstance(language, str) and language in ("zh", "en"), "unsupported query language")
            normalized["language"] = language
        result.append(normalized)
    return result


def _content_digest(queries: list[dict[str, Any]]) -> str:
    content = [{key: row[key] for key in CONTENT_FIELDS} for row in queries]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _verify_manifest(path: Path, raw: bytes, queries: list[dict[str, Any]]) -> str:
    manifest, manifest_raw = _read_object(path.with_name("manifest.json"))
    fields = {"version", "dataset_id", "queries_file", "hash_format", "queries_sha256", "query_count",
              "query_ids", "category_counts", "language_counts", "split_counts", "holdout_ids",
              "baseline_content_sha256", "baseline_input_sha256", "relevance_labels", "ranking_evaluation"}
    _need(manifest.keys() == fields, "frozen manifest has missing or unknown fields")
    _need(type(manifest["version"]) is int and manifest["version"] == 1, "unsupported manifest version")
    _need(manifest["dataset_id"] == DATASET_ID and manifest["queries_file"] == path.name,
          "manifest dataset or query filename mismatch")
    _need(manifest["hash_format"] == "sha256-utf8-lf", "unsupported manifest hash format")
    for field in ("queries_sha256", "baseline_content_sha256", "baseline_input_sha256"):
        _need(isinstance(manifest[field], str) and bool(re.fullmatch(r"[0-9a-f]{64}", manifest[field])),
              f"invalid manifest {field}")
    _need(manifest["queries_sha256"] == hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
          "frozen query input hash mismatch")
    _need(manifest["baseline_content_sha256"] == _content_digest(queries),
          "original query IDs, text or classification changed")
    _need(type(manifest["query_count"]) is int and manifest["query_count"] == len(queries) == 40,
          "frozen dataset must contain exactly 40 queries")
    _need(manifest["query_ids"] == [q["id"] for q in queries], "frozen query ID order mismatch")
    holdout = [q["id"] for q in queries if q["split"] == "holdout"]
    _need(manifest["holdout_ids"] == holdout == ["h01", "h02", "h03", "h04", "h05"],
          "frozen holdout IDs mismatch")
    for field, query_field in (("category_counts", "category"), ("language_counts", "language"),
                               ("split_counts", "split")):
        counts = manifest[field]
        _need(isinstance(counts, dict) and all(type(v) is int and v > 0 for v in counts.values()) and
              counts == dict(Counter(q[query_field] for q in queries)), f"manifest {field} mismatch")
    _need(manifest["relevance_labels"] == "not_provided" and
          manifest["ranking_evaluation"] == "not_executed",
          "golden manifest does not certify relevance labels or ranking evaluation")
    return hashlib.sha256(manifest_raw).hexdigest()


def load_golden(path: str | Path) -> GoldenSet:
    """Load the frozen dataset or a bounded legacy probe fixture, without I/O beyond files."""
    path = Path(path)
    value, raw = _read_object(path)
    _need(not value.keys() - {"version", "dataset_id", "notes", "queries"}, "unknown golden dataset field")
    version = value.get("version")
    if "version" in value:
        _need(type(version) is int and version == 1, "unsupported golden dataset version")
    if "notes" in value:
        _text(value["notes"], "dataset notes", 4096)
    # A neighbouring freeze manifest cannot be bypassed by deleting dataset_id.
    # Legacy query fixtures may share a directory with a source manifest, which
    # is unrelated to query freezing and must not silently change their format.
    frozen = "dataset_id" in value
    if not frozen and path.with_name("manifest.json").exists():
        adjacent, _ = _read_object(path.with_name("manifest.json"))
        frozen = adjacent.get("dataset_id") == DATASET_ID
    if frozen:
        _need(value.get("dataset_id") == DATASET_ID and version == 1, "unsupported frozen dataset")
    queries = _normalize_queries(value.get("queries"), frozen=frozen)
    manifest_digest = _verify_manifest(path, raw, queries) if frozen else None
    return GoldenSet(queries=queries, digest=hashlib.sha256(raw).hexdigest(), version=version,
                     frozen=frozen, manifest_digest=manifest_digest)


def select_queries(queries: list[dict[str, Any]], capability: str, limit: int) -> list[dict[str, Any]]:
    """Select matching non-holdout queries, round-robin over sorted categories.

    Input order is retained within each category. Empty capability matches return
    an empty list; there is no unrelated-query fallback or automatic holdout use.
    """
    _need(isinstance(capability, str) and capability in CAPABILITIES, "unsupported source capability")
    _need(type(limit) is int and 1 <= limit <= 40, "query selection limit must be in 1..40")
    groups: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for query in _normalize_queries(queries):
        if query["split"] != "holdout" and capability in query["good_for"]:
            groups[query["category"]].append(query)
    categories = sorted(groups)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progressed = False
        for category in categories:
            if groups[category]:
                selected.append(groups[category].popleft())
                progressed = True
                if len(selected) == limit:
                    break
        if not progressed:
            break
    return selected
