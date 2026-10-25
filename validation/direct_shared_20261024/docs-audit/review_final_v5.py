"""Read-only final document/interface audit of the parent's v5 frozen inputs."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
assert not (out / "audit-final-v5.json").exists(), "Keep completed audit evidence immutable."
baseline = "346048b4ae329e750d15f1e35952aab4513e3912"
binding_path = root / "validation/direct_shared_20261024/build/candidate-v5-inputs.json"
binding = json.loads(binding_path.read_text())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def features_bytes(text):
    start = text.index('"features":') + len('"features":')
    while text[start].isspace():
        start += 1
    _, used = json.JSONDecoder().raw_decode(text[start:])
    return text[start:start + used].encode()


def baseline_bytes(path):
    return subprocess.check_output(["git", "show", baseline + ":" + path], cwd=root)


actual_hashes = {p: digest((root / p).read_bytes()) for p in binding["files"]}
changed = [p for p, sha in actual_hashes.items() if sha != binding["files"][p]]
assert not changed, changed
input_record = {
    "frozen_binding": str(binding_path.relative_to(root)),
    "binding_sha256": digest(binding_path.read_bytes()),
    "base_commit": binding["base_commit"],
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "all_current_files_match_v5_binding": True,
    "files": actual_hashes,
}
(out / "all-current-inputs-v5.json").write_text(json.dumps(input_record, ensure_ascii=False, indent=2) + "\n")

document_paths = [
    "README.md", "HANDOFF_START_HERE.md", "docs/API.md", "docs/DIRECT_SHARED_READS.md",
    "docs/SHARED_READS.md", "docs/GITHUB_HANDOFF.md", "docs/DEVELOPMENT_STATUS.md",
    "docs/handoff/NEXT_AGENT.md",
]
documents = {p: (root / p).read_text() for p in document_paths}
status_text = (root / "docs/implementation_status.json").read_text()
status = json.loads(status_text)
old_status = baseline_bytes("docs/implementation_status.json").decode()
feature_raw = features_bytes(status_text)
assert feature_raw == features_bytes(old_status)
assert digest(feature_raw) == "f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d"
assert len(feature_raw) == 122468
assert "features[].candidate_increment" in status["feature_detail_text_scope"]["frozen_fields"]
assert "features[].remaining" in status["feature_detail_text_scope"]["frozen_fields"]
assert "历史候选说明" in status["feature_detail_text_scope"]["meaning"]

plan_path = "docs/planning/slices_v1.2.json"
plan_bytes = (root / plan_path).read_bytes()
assert plan_bytes == baseline_bytes(plan_path)
plan = json.loads(plan_bytes)
assert len(plan["packages"]) == len(status["features"]) == 83
assert len(plan["slices"]) == 10 and len(plan["acceptance_mapping"]) == 161
assert Counter(f["status"] for f in status["features"]) == Counter({"partial": 37, "not_started": 46})
assert status["status_counts"] == {"partial": 37, "not_started": 46, "accepted": 0}
assert all(f["full_work_package_accepted"] is False for f in status["features"])
assert status["planned_acceptance_suite_executed"] is False
assert status["full_work_packages_accepted"] == 0
acceptance = {item["id"]: item for item in plan["acceptance_mapping"]}
assert acceptance["FT-032"]["feature_ids"] == ["CACHE-01"]
assert acceptance["B-24"]["feature_ids"] == ["CACHE-01"]
assert acceptance["FT-019"]["feature_ids"] == ["MODE-01"]
assert set(status["direct_shared_reads_scope"]["acceptance_ids"]) == {"FT-032", "B-24"}
assert all(acceptance[key]["execution_status"] == "not_signed_off_for_0.1.10" for key in ("FT-032", "B-24", "FT-019"))
sources = json.loads((root / "config/sources.json").read_text())["sources"]
sites = json.loads((root / "config/sites.json").read_text())["sites"]
assert len(sources) == 17 and not any(s["enabled"] for s in sources) and not sites

proposal = json.loads((out / "final-docs-proposal.json").read_text())
applied = []
old_projection = "组中保存提交所得快照的序列化 JSON，不把可变 JSON 对象交给并发成员共用。各成员独立反序列化这份快照描述，使用自己的 `start_byte`、`max_bytes` 和 `max_age_s` 构造响应。付款成员的窗口参数错误不会使已经提交的表示失败，其他成员的有效窗口仍可返回；订阅成员的窗口错误也不污染付款成员。"
new_projection = "组中只保存窗口所需快照字段的独立序列化 JSON，不把可变 JSON 对象交给并发成员共用。各成员独立重建这份描述，正文由 `Store::range` 读取；不再复制正文与发布片段的聚合 JSON，避免给合法旧响应新增聚合字节或节点上限。各成员使用自己的 `start_byte`、`max_bytes` 和 `max_age_s` 构造响应。付款成员的窗口参数错误不会使已经提交的表示失败，其他成员的有效窗口仍可返回；订阅成员的窗口错误也不污染付款成员。"
for item in proposal["files"]:
    p = item["path"]
    text = (root / p).read_text()
    if p == "docs/DIRECT_SHARED_READS.md":
        assert text.count(new_projection) == 1
        assert digest(text.replace(new_projection, old_projection, 1).encode()) == item["proposed_sha256"]
        note = "Parent applied the proposal plus the exact v5 narrow-field serialization paragraph."
    else:
        assert digest(text.encode()) == item["proposed_sha256"], p
        note = "Exactly matches the proposed documentation-only bytes."
    applied.append({"path": p, "sha256": digest(text.encode()), "status": note})

web = (root / "src/web.cpp").read_text()
header = (root / "include/bzs/web.hpp").read_text()
store = (root / "src/store.cpp").read_text()
service = (root / "src/service.cpp").read_text()
projection = re.search(r'for\(const auto\* field:\{([^}]+)\}\)\s*flight->snapshot_fields\.emplace', web)
assert projection
projection_fields = re.findall(r'"([^"]+)"', projection.group(1))
expected_fields = ["snapshot_id", "document_id", "url", "title", "revision", "indexed", "site_publication", "meta", "links"]
assert projection_fields == expected_fields
window = web.split("Json WebService::window(", 1)[1].split("Json WebService::read(", 1)[0]
assert set(re.findall(r'snap\.at\("([^"]+)"\)', window)) == set(expected_fields)
assert "store_.range(" in window
assert "std::map<std::string,std::string> snapshot_fields;" in header
assert "Json::parse(encoded,encoded.size())" in web
assert "snapshot_json" not in web and "snapshot_json" not in header
assert "found->second->members<32" in web and "direct_reads_.size()<64" in web
assert "++flight->members" in web and "--flight->members" not in web
assert '"known",!payer||physical.at("known").flag()' in web
assert 'payer?physical.at(key):Json::integer(0)' in web
assert 'require(actual_profile==profile,"E_READ_PROFILE_CHANGED"' in web
assert 'if(members>1)' in web
assert '"E_REVALIDATION_STALE","Read response metadata changed before delivery"' in store
assert 'exec(db_, "BEGIN IMMEDIATE")' in store
assert 'seal_publication_locked(policy,publication_history_locked' in store
assert 'store_.require_principal_active(p.subject);\n    return result;' in service
assert "外层 `Service::dispatch`" in documents["docs/DIRECT_SHARED_READS.md"]
assert "outer Service principal postcheck can omit details" in status["direct_shared_reads_scope"]["accounting"]
assert "缺少可靠物理回执时" in documents["docs/API.md"]
assert "`subscriber` 增量仍为 `known=true`" in documents["docs/API.md"]
assert "原默认分支发布" not in documents["README.md"]
assert "本轮维持默认分支发布的自动审批阻塞" not in documents["README.md"]
assert "远端 main 仍为 `ef45490" not in documents["README.md"]
assert "用户已明确允许推送 main" in documents["README.md"]

historical_markers = {
    "HANDOFF_START_HERE.md": "## 上一批开发入口（发布前历史）",
    "docs/GITHUB_HANDOFF.md": "## 上一批发布前接续",
    "docs/DEVELOPMENT_STATUS.md": "## 以下为上一批开发记录（发布前历史）",
    "docs/handoff/NEXT_AGENT.md": "## 以下为上轮接手记录（发布前历史）",
}
prospective_counts = []
for p, text in documents.items():
    current = text.split(historical_markers[p], 1)[0] if p in historical_markers else text
    for match in re.finditer(r"(?<![0-9A-Za-z])(34|23|1294|1,294|1，294)(?![0-9A-Za-z])\s*(?:组|项|套|cases|tests|passed|通过|/)", current):
        prospective_counts.append({"path": p, "text": match.group(0)})
assert not prospective_counts, prospective_counts

allowed_pending = {
    root / "validation/direct_shared_20261024/README.md",
    root / "validation/direct_shared_20261024/final-report.json",
}
link_records = []
for p, text in documents.items():
    for match in re.finditer(r"\]\(([^)]+)\)", text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        resolved = ((root / p).parent / target.split("#", 1)[0]).resolve()
        exists = resolved.exists()
        assert exists or resolved in allowed_pending, (p, target)
        link_records.append({"path": p, "line": text.count("\n", 0, match.start()) + 1, "target": target,
                             "status": "present" if exists else "parent_report_pending_not_a_bug"})
(out / "links-final-v5.json").write_text(json.dumps(link_records, ensure_ascii=False, indent=2) + "\n")

findings = [
    {"id": "DOC-01", "issue": "Unqualified current CACHE-01 candidate_increment/remaining text retained historical dispatch-only descriptions.", "resolution": "Parent added feature_detail_text_scope outside the untouched features array, explicitly naming historical fields and current late/direct scopes.", "status": "resolved"},
    {"id": "DOC-02", "issue": "Handoff and summaries overpromised receipts on every direct failure.", "resolution": "Handoff, development summary, current machine scope and direct contract now limit the promise to actual execution details and record the outer Service boundary.", "status": "resolved"},
    {"id": "DOC-03", "issue": "README still presented old local late-read candidate, ef45490 remote main and publication approval block as current.", "resolution": "Parent applied the current direct-read baseline and authorization wording, without claiming a new containing commit or future CI.", "status": "resolved"},
    {"id": "DOC-04", "issue": "API unknown wording did not distinguish unknown physical/payer usage from subscriber known-zero incremental usage.", "resolution": "API now explicitly distinguishes both fields and retains nonadditive association.", "status": "resolved"},
    {"id": "DOC-05", "issue": "API envelope opening implied every business failure contains result and usage.", "resolution": "Success and failure shapes are now separated; optional failure details are not fabricated.", "status": "resolved"},
    {"id": "DOC-06", "issue": "The old 304 API paragraph overgeneralized one request/zero bytes, durable reservation semantics and redirect rejection.", "resolution": "The paragraph now distinguishes direct versus authorized routed 304, direct unknown receipts versus durable reservations, and invalid route proofs.", "status": "resolved"},
    {"id": "DOC-07", "issue": "Whole-snapshot serialization description was superseded by the parent's large-response compatibility fix.", "resolution": "The final contract describes independently encoded window fields and Store body reads; the source uses exactly the nine fields consumed by window.", "status": "resolved_as_static_contract_review_runtime_validation_owned_by_parent"},
]
checks = [
    {"topic": "Identity", "result": "Same WebService, same owner, exact normalized URI, actual timeout/conditional request, complete fence, cache snapshot/metadata and current valid profile agree across API/contract/current scope.", "source": ["src/web.cpp: WebService::read", "src/publication.cpp: Store::publication_fence"]},
    {"topic": "Capacity and lifecycle", "result": "32 cumulative members including payer; 64 open-admission map entries, not a total physical execution cap. Overflow/reentry/unproven requests remain independent; RPC completion closes admission.", "source": ["src/web.cpp: direct_reads_ admission and close_admission", "include/bzs/web.hpp: DirectReadFlight"]},
    {"topic": "Profile", "result": "The optional network-path profile proof may fail into independent execution. Multi-member success requires valid actual http_freshness with matching request_profile; single-member legacy behavior remains.", "source": ["src/web.cpp: optional read profile and members > 1 validation"]},
    {"topic": "Waiting and cancellation", "result": "Subscriber steady_clock wait is timeout plus 5000 ms; no automatic reissue or direct fetch.cancel; there is no public direct-member cancel handle or durable recovery ledger.", "source": ["src/web.cpp: subscriber wait_until and receipt", "docs/HTTP_EXECUTION.md"]},
    {"topic": "Nine-field transfer", "result": "Each window field is independently serialized. Members reconstruct their own Json values and read text through Store::range. No full canonical_text/publication aggregate is passed to the former 8 MiB parse.", "fields": projection_fields, "source": ["src/web.cpp: snapshot_fields population and reconstruction", "include/bzs/web.hpp: DirectReadFlight", "src/web.cpp: WebService::window"]},
    {"topic": "304 and delivery", "result": "One physical ingest; each result window uses BEGIN IMMEDIATE for auth epoch, deletion/all aliases, metadata digest, current and historical publication projection. A changed same-ID metadata record fails E_REVALIDATION_STALE; new no-cache results do not require cache-hit TTL.", "source": ["src/store.cpp: Store::ingest_web_locked", "src/store.cpp: Store::range", "src/publication.cpp: publication history and alias fences"]},
    {"topic": "Known and unknown usage", "result": "Payer is actual or null unknown; subscriber incremental usage is known zero even when physical association is unknown. Cache hits are zero with no shared_read; network members retain from_cache=false. Associated physical usage is not additive.", "source": ["src/web.cpp: direct_read_usage", "src/web.cpp: direct_read_increment", "src/web.cpp: cached_window and receipt"]},
    {"topic": "Error transport and MCP", "result": "HTTP only preserves available execution details. CoreClient retains only dict usage/shared_read; MCP forwards present success fields and appends compact detail JSON to the original ToolError message without recomputation.", "source": ["include/bzs/error.hpp", "apps/searchd/main.cpp: failure", "python/bzs/core_client.py: CoreError and _unwrap", "python/bzs/mcp_server.py: _call and read_url"]},
    {"topic": "Restrictive observations", "result": "No-store remains an error; invalid accounting/profile/cache proofs do not suppress separately validated restrictive observations. Cache invalidation is bound to the original snapshot and metadata digest.", "source": ["src/web.cpp: receipt_error and apply_observations", "src/store.cpp: invalidate_web_cache", "src/publication.cpp: observe_web_publication"]},
    {"topic": "Durable separation", "result": "SHARED_READS is explicitly scoped to durable read_many; late first-item admission, grants, lease capture, persistent closure and unknown settlement remain separate from direct flights.", "source": ["docs/SHARED_READS.md", "src/batch.cpp: attach_late_read_locked and shared attempt storage", "src/web.cpp: execute_read"]},
    {"topic": "Plan and non-acceptance", "result": "FT-032/B-24 map to CACHE-01, FT-019 maps to MODE-01. Original feature bytes and planned mapping are preserved; 83/161/10 and 37/46/0 remain, no full work-package acceptance or future CI is asserted.", "source": ["docs/planning/slices_v1.2.json", "docs/implementation_status.json", "current handoff sections"]},
]
report = {
    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "closed_read_only_static_document_interface_audit",
    "runtime_tests_executed_by_this_audit": False,
    "network_calls_executed_by_this_audit": False,
    "baseline": baseline,
    "current_binding": str(binding_path.relative_to(root)),
    "all_bound_input_hashes_match": True,
    "bound_file_count": len(actual_hashes),
    "input_record": "all-current-inputs-v5.json",
    "document_hashes": {p: actual_hashes[p] for p in [*document_paths, "docs/implementation_status.json"]},
    "features": {"bytes": len(feature_raw), "sha256": digest(feature_raw), "equal_to_baseline_bytes": True},
    "plan": {"original_bytes_preserved": True, "work_packages": 83, "acceptance_items": 161, "slices": 10, "partial": 37, "not_started": 46, "accepted": 0},
    "configuration": {"version": status["version"], "schema": status["direct_shared_reads_scope"]["schema"], "registered_sources": 17, "enabled_sources": 0, "registered_sites": 0},
    "applied_parent_document_corrections": applied,
    "findings": findings,
    "interface_checks": checks,
    "remaining_boundaries": [
        {"id": "RECEIPT-OUTER-SERVICE", "description": "The final Service::dispatch principal check can throw after WebService completed; that outer Error may omit execution details although physical work occurred. Documentation now states this explicitly. This batch does not add a durable direct ledger or repair that receipt gap.", "source": ["src/service.cpp: Service::dispatch", "docs/DIRECT_SHARED_READS.md: error-envelope boundary"]},
        {"id": "GENERAL-SHARING", "description": "Cross-instance/process and direct/durable/map/crawl/search sharing, public direct-member cancellation, restart receipt recovery and full CACHE-01/B-24 acceptance remain outside the implemented scope."},
    ],
    "link_check": {"local_link_occurrences": len(link_records), "unexpected_missing": [], "parent_report_placeholders": [x for x in link_records if x["status"] != "present"]},
    "prospective_runtime_counts_in_current_docs": prospective_counts,
    "limitations": [
        "This is a static consistency audit, not a new native/Python/CTest/ASan result, performance claim or acceptance sign-off.",
        "Published-baseline GitHub claims were not independently re-queried; current runtime and CI outcomes belong to the parent's actual bound execution records.",
        "The parent-owned final validation README/final-report may still be pending; those exact link targets are not classified as defects.",
    ],
}
assert all(digest((root / p).read_bytes()) == sha for p, sha in actual_hashes.items())
(out / "audit-final-v5.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
human = """# 最终文档与接口一致性审计

状态：v5 冻结输入的只读静态审计已关闭。全部绑定文件的实际 SHA-256 与 `build/candidate-v5-inputs.json` 一致，逐文件记录见 `all-current-inputs-v5.json`。本审计没有运行原生、Python、CTest 或 ASan 测试，也没有请求外部服务；不把正在执行的门禁或未来 CI 计作成功。

## 已关闭的文档问题

- 保留 `features` 数组原始字节，在数组外明确 `candidate_increment` 与 `remaining` 是历史候选文本；当前批读迟到加入和 direct 范围分别以顶层 scope 和合同为准。
- README 已移除把旧本地候选、ef45490 和发布审批阻塞当作当前状态的表述，采用已发布基线及本轮直接读取范围。
- API、交接入口、开发状态和机器状态只承诺透传已有执行详情。成功与失败信封的字段资格、subscriber 已知零增量与未知物理关联已明确区分。
- API 的旧 304 段明确直达与有授权跳转的计费差异，以及普通 direct 无持久预留、未知为 null 的边界。
- 新大正文修正与合同一致：共享组只独立编码 snapshot_id、document_id、url、title、revision、indexed、site_publication、meta、links 九个字段；正文仍由 Store::range 读取，不再构造重复正文和发布片段的聚合快照解析输入。

对应原始问题、父线程应用的补丁及精确前后摘要保留于本目录。`audit-final-v5.json` 逐项记录实现位置与核对结论，未改写最初审计输入或旧失败证据。

## 合同核对结论

完整 owner/fence/精确 URI/实际请求参数/profile/缓存 metadata 身份，32 累计成员、64 个开放加入组，RPC 完成关闭加入、同线程重入独立执行，与当前实现一致。64 是开放登记容量，并非总物理请求上限。

可选 profile 失败走独立网络路径，多人成功正文及 304 的真实 profile 必须匹配；单成员保留旧 Worker 合同。订阅者单调等待超时不会自动重发或向 direct 尝试发 fetch.cancel。共享不跨 WebService，也不与持久 read_many、map、crawl 或 search 混合。

每个网络结果窗口在 Store 的 BEGIN IMMEDIATE 事务中重新检查主体代际、删除和全部实际别名、准确 metadata、当前及历史发布投影。相同快照 ID 的后续 304 metadata 不与旧物理收据混合；正文只提交一次，窗口独立。no-store 仍报错，畸形收据不绕过独立有效的限制性观察。

FT-032 / B-24 正确映射 CACHE-01；FT-019 仍属 MODE-01。原 features 数组为 122468 bytes，SHA-256 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`，与基线完全相同。规划字节、83 包 / 161 验收 / 10 切片、37 partial / 46 not_started / 0 accepted、0.1.10 / schema15、17 来源 / 0 启用 / 0 站点保持。当前文档没有预填本轮运行成功数量；历史成绩仍由历史标题限定。

## 明确保留的边界

`Service::dispatch` 在 WebService 返回后仍执行主体有效性终检。该外层检查失败时可能返回无 Error.details 的错误，即使物理工作已经发生。因此完整 HTTP 失败面并不保证都能取回消耗详情；缺字段既不是零消耗，也不是安全重试证明。此轮仅把该边界写清，没有新增持久 direct 用量账本或修复这项外层收据缺口。

跨实例/进程协调、direct 与持久通道的统一共享、direct 单成员取消、重启后的执行收据恢复和完整 CACHE-01/B-24 签收仍未完成。本审计不代替这些能力的运行验收。

所有已存在的本地链接已核对；本批父线程待生成的 README 与 final-report 是明确允许的报告占位，不作为缺陷。远端 Checks 未由本次只读审计重新查询，实际门禁和发布结果只以父线程绑定的运行记录为准。
"""
(out / "audit-final-v5.md").write_text(human)
print(json.dumps({"status": report["status"], "bound_files": len(actual_hashes), "all_hashes_match": True,
                  "feature_sha256": digest(feature_raw), "projection_fields": projection_fields,
                  "resolved_document_findings": len(findings), "runtime_tests_run": False,
                  "report_sha256": digest((out / "audit-final-v5.json").read_bytes())}, ensure_ascii=False))
