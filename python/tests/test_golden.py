import copy
from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from bzs.golden import MAX_INPUT_BYTES, load_golden, select_queries

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden"
BASELINE_CONTENT_SHA256 = "46b9b603d48e3ebcccf7b1b74dfffbbabfd9f473935fb4536f4903c67b1f6b96"


def write_queries(tmp_path, value):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def copy_frozen(tmp_path):
    for name in ("queries.json", "manifest.json"):
        (tmp_path / name).write_bytes((GOLDEN / name).read_bytes())
    return tmp_path / "queries.json"


def test_frozen_dataset_preserves_original_content_and_separates_splits():
    path = GOLDEN / "queries.json"
    loaded = load_golden(path)
    content = [{key: q[key] for key in ("id", "query", "category", "language", "good_for")}
               for q in loaded.queries]
    content_bytes = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(content_bytes).hexdigest() == BASELINE_CONTENT_SHA256
    assert loaded.frozen is True and loaded.version == 1
    assert loaded.digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.manifest_digest == hashlib.sha256((GOLDEN / "manifest.json").read_bytes()).hexdigest()
    assert len(loaded.queries) == 40
    assert Counter(q["split"] for q in loaded.queries) == {"development": 20, "regression": 15, "holdout": 5}
    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["relevance_labels"] == "not_provided"
    assert manifest["ranking_evaluation"] == "not_executed"


def test_default_selection_covers_categories_without_holdout_or_mutation():
    queries = load_golden(GOLDEN / "queries.json").queries
    original = copy.deepcopy(queries)
    selected = select_queries(queries, "web", 8)
    assert len(selected) == 8 and len({q["category"] for q in selected}) == 8
    assert {q["language"] for q in selected} == {"en", "zh"}
    assert all("web" in q["good_for"] and q["split"] != "holdout" for q in selected)
    assert selected == select_queries(queries, "web", 8)
    selected[0]["good_for"].clear()
    assert queries == original


def test_all_capabilities_keep_the_five_holdout_ids_unselected():
    queries = load_golden(GOLDEN / "queries.json").queries
    for capability in ("web", "papers", "encyclopedia", "code", "news"):
        selected = select_queries(queries, capability, 40)
        assert selected and all(capability in q["good_for"] for q in selected)
        assert len({q["id"] for q in selected}) == len(selected)
        assert not any(q["id"].startswith("h") or q["split"] == "holdout" for q in selected)


def test_legacy_fixtures_remain_explicitly_unfrozen_and_do_not_fallback(tmp_path):
    path = write_queries(tmp_path, {"queries": [
        {"id": "q1", "query": "resumable crawling", "good_for": ["web"]},
        {"id": "q2", "query": "断点续爬"},
        {"id": "h01", "query": "reserved holdout", "good_for": ["web"]},
    ]})
    loaded = load_golden(path)
    assert loaded.frozen is False and loaded.version is None and loaded.manifest_digest is None
    assert [q["id"] for q in select_queries(loaded.queries, "web", 40)] == ["q1", "q2"]
    assert select_queries(loaded.queries, "papers", 8) == []


def test_category_round_robin_resumes_after_each_category_and_respects_limit():
    queries = [{"id": ident, "query": ident, "category": category, "good_for": ["web"]}
               for ident, category in [("q1", "zh_tech"), ("q2", "exact_symbol"), ("q3", "zh_tech"),
                                       ("q4", "navigational"), ("q5", "exact_symbol"), ("q6", "zh_tech")]]
    assert [q["id"] for q in select_queries(queries, "web", 5)] == ["q2", "q4", "q1", "q5", "q3"]


def test_legacy_query_file_can_share_a_directory_with_a_source_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text('{"version":1,"sources":[]}', encoding="utf-8")
    loaded = load_golden(write_queries(tmp_path, {"queries": [{"id": "q1", "query": "x", "good_for": ["web"]}]}))
    assert loaded.frozen is False
    assert [q["id"] for q in select_queries(loaded.queries, "web", 1)] == ["q1"]


@pytest.mark.parametrize("raw", [
    b'{"queries":[],"queries":[]}',
    b'{"queries":[{"id":"q1","id":"q2","query":"x"}]}',
    b'{"version":NaN,"queries":[]}',
    b'{"version":Infinity,"queries":[]}',
    b'\xff',
    b'[]',
])
def test_rejects_duplicate_keys_non_json_constants_and_invalid_encoding(tmp_path, raw):
    path = tmp_path / "queries.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_golden(path)


@pytest.mark.parametrize("value", [
    {"queries": []}, {"queries": "q1"}, {"queries": [None]},
    {"version": True, "queries": [{"id": "q", "query": "x"}]},
    {"version": 1.0, "queries": [{"id": "q", "query": "x"}]},
    {"version": 2, "queries": [{"id": "q", "query": "x"}]},
    {"unexpected": True, "queries": [{"id": "q", "query": "x"}]},
    {"queries": [{"id": "q", "query": "x"}, {"id": "q", "query": "y"}]},
])
def test_rejects_invalid_dataset_shapes_and_versions(tmp_path, value):
    with pytest.raises(ValueError):
        load_golden(write_queries(tmp_path, value))


@pytest.mark.parametrize("replacement", [
    {"id": "../escape"}, {"id": ""}, {"id": False}, {"id": "a" * 81},
    {"query": " "}, {"query": None}, {"query": {"text": "x"}}, {"query": "x" * 1001},
    {"query": "x\ncommand"}, {"query": "\ud800"},
    {"good_for": "web"}, {"good_for": []}, {"good_for": ["web", "web"]},
    {"good_for": ["unsupported"]}, {"good_for": [True]},
    {"category": []}, {"category": "unknown"}, {"language": "xx"},
    {"split": None}, {"split": "training"},
    {"id": "h01", "split": "development"}, {"category": "holdout", "split": "regression"},
    {"relevance_label": 3},
])
def test_rejects_bad_query_types_limits_and_holdout_overrides(tmp_path, replacement):
    query = {"id": "q1", "query": "x", "good_for": ["web"]}
    query.update(replacement)
    with pytest.raises(ValueError):
        load_golden(write_queries(tmp_path, {"queries": [query]}))


def test_enforces_input_byte_and_query_count_limits(tmp_path):
    path = tmp_path / "queries.json"
    path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_golden(path)
    path = write_queries(tmp_path, {"queries": [{"id": f"q{i}", "query": "x"} for i in range(1001)]})
    with pytest.raises(ValueError, match="1..1000"):
        load_golden(path)


@pytest.mark.parametrize("capability, limit", [(None, 1), ([], 1), ("unknown", 1),
                                               ("web", True), ("web", 0), ("web", 41), ("web", 1.0)])
def test_selection_rejects_unknown_capabilities_and_noninteger_limits(capability, limit):
    with pytest.raises(ValueError):
        select_queries([{"id": "q1", "query": "x"}], capability, limit)


def test_frozen_input_change_is_rejected_without_rewriting_manifest(tmp_path):
    path = copy_frozen(tmp_path)
    manifest_before = (tmp_path / "manifest.json").read_bytes()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["queries"][0]["query"] = "changed query"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_golden(path)
    assert (tmp_path / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize("field, value", [("query_count", True), ("query_count", 39),
                                         ("query_ids", []), ("holdout_ids", []),
                                         ("category_counts", {"academic": True}),
                                         ("split_counts", {"development": 40}),
                                         ("baseline_content_sha256", "0" * 64),
                                         ("relevance_labels", "approved"),
                                         ("ranking_evaluation", "passed"),
                                         ("unexpected", "field")])
def test_frozen_manifest_refuses_changed_counts_identity_and_false_acceptance(tmp_path, field, value):
    path = copy_frozen(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data[field] = value
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden(path)


def test_frozen_dataset_cannot_downgrade_by_deleting_its_marker(tmp_path):
    path = copy_frozen(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["dataset_id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen dataset"):
        load_golden(path)


@pytest.mark.parametrize("checkout_eol", [b"\n", b"\r\n"], ids=["lf-checkout", "crlf-checkout"])
def test_missing_manifest_fails_and_crlf_checkout_keeps_exact_input_digest(tmp_path, checkout_eol):
    path = copy_frozen(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.unlink()
    with pytest.raises(FileNotFoundError):
        load_golden(path)
    path = copy_frozen(tmp_path)
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", checkout_eol))
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    loaded = load_golden(path)
    assert loaded.frozen is True
    assert loaded.digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.digest != json.loads(manifest.read_text(encoding="utf-8"))["queries_sha256"]
