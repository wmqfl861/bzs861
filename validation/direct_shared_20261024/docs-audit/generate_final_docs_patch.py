"""Prepare documentation-only corrections; never apply them to frozen inputs."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
before = {}
after = {}
changes = []


def replace(path, old, new, reason):
    if path not in before:
        before[path] = (root / path).read_text()
        after[path] = before[path]
    assert after[path].count(old) == 1, (path, old)
    after[path] = after[path].replace(old, new, 1)
    changes.append({"path": path, "reason": reason, "old": old, "new": new})


replace("README.md",
        "**状态：持久批读迟到订阅的本地候选；版本仍为 0.1.10，数据库 schema15。** 本批从上轮已本地验证、未推送的 `a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89` 接续，允许兼容新批读任务的首项加入原未结算尝试，保留固定付款、原发布捕获、租约和 32 个累计成员上限。范围与升级合同见[共享读取](docs/SHARED_READS.md)，基线失败、作者验证、独立审核和整合执行见[本批验证记录](validation/shared_read_late_20261024/README.md)。该合同与证据入口不预先声明新测试通过；S0、S1、S2 的完整验收仍未完成。",
        "**状态：普通直接读取的进程内共享与同步用量回执；版本仍为 0.1.10，数据库 schema15。** 本批从已发布的 `346048b4ae329e750d15f1e35952aab4513e3912` 接续，同一 `WebService` 内兼容的直接读取共享一次获取和一次表示提交，各成员独立取得窗口并通过返回屏障。合同见[直接共享读取](docs/DIRECT_SHARED_READS.md)，实际输入、失败与验证见[本批记录](validation/direct_shared_20261024/README.md)；已发布的持久批读迟到加入继续按[持久共享读取](docs/SHARED_READS.md)执行，两条通道不混合。完整 B-24、CACHE-01 及 S0、S1、S2 验收仍未完成。",
        "Replace an unqualified obsolete current-batch description with the actual direct-read scope and baseline.")
replace("README.md",
        "目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。从[当前开发入口](HANDOFF_START_HERE.md)开始。远端 main 仍为 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`，该提交的 [CI 40096268557](https://github.com/wmqfl861/bzs861/actions/runs/40096268557) 是历史成绩；`a3b3f6e2` 不是远端 main。本轮维持默认分支发布的自动审批阻塞，不重试或绕过远端写入，不声明新 CI 或新 tag。升级数据库前停止旧 Core，新旧程序不能混用。",
        "目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。从[当前开发入口](HANDOFF_START_HERE.md)开始。上述已发布基线的 [CI 40157161849](https://github.com/wmqfl861/bzs861/actions/runs/40157161849) 只证明该基线输入。用户已明确允许推送 main；本批包含提交和该提交自己的 CI 以实际发布记录为准，不预写新 CI 成功或新 tag。本增量没有数据库迁移；需要升级数据库时仍须停止旧 Core，新旧格式进程不能混用。",
        "Remove obsolete remote-main and publication-blocker assertions without claiming a future containing commit or CI result.")
replace("README.md",
        "| 网页读取候选实现 | 缓存指令独立于 validator；直达或有完整证明的 HTTP 链按年龄/profile 复用，逐跳期限、删除和发布约束保持；no-store 无新持久快照 |",
        "| 网页读取候选实现 | 同一 WebService 内兼容直接读取可共享一次在途获取与提交，各自窗口和用量关联独立；缓存指令独立于 validator，完整直达/HTTP 链按年龄/profile 复用；no-store 无新持久快照 |",
        "Expose the current direct-read increment in the capability table.")
replace("docs/API.md",
        "响应信封包含 `request_id`、`api_version`、`status`、`result`、`errors` 和 `usage`。",
        "成功响应信封包含 `request_id`、`api_version`、`status`、`result`、`errors` 和 `usage`。失败信封保留请求身份、失败状态和 `errors`，不要求存在 `result`；`usage`、`shared_read` 仅在核心提供执行详情时输出，缺少字段不能解释为零消耗。",
        "Do not promise result or usage fields on every business failure.")
replace("docs/API.md",
        "缺少可靠回执时 `known=false`、请求数/字节为 `null`；缓存命中是已知零用量且没有 `shared_read`。",
        "缺少可靠物理回执时，`physical_usage` 与 `payer` 的增量为 `known=false`、请求数/字节为 `null`；`subscriber` 增量仍为 `known=true`、零请求和零字节，同时保留未知物理关联。缓存命中是已知零用量且没有 `shared_read`。",
        "Disambiguate unknown physical/payer usage from the subscriber's known zero increment.")
replace("docs/API.md",
        "已知304仍计1个实际HTTP请求、0正文响应字节；未知或无法解释的执行按既有保守预留结算。未经绑定、validator冲突、跳转后的304或并发替换失败，不在同一次授权中额外补抓完整GET。未保存完整可重建链接的crawl页面采用原完整请求路径。",
        "已知用量的直达304计1个实际HTTP请求、0正文响应字节；具有完整路由授权的N跳304计N+1次请求，并计入已消费的跳转响应体，详见[跳转缓存](HTTP_REDIRECT_CACHE.md)。未知执行在持久任务中按既有保守预留结算；普通直接读取没有该预留账本，物理回执保留 `known=false` 和 `null`，订阅者仍只报告零增量。未经绑定、validator冲突、缺少合法路由证明的跳转304或并发替换失败，不在同一次授权中额外补抓完整GET。未保存完整可重建链接的crawl页面采用原完整请求路径。",
        "Qualify older 304 accounting/rejection text for routed validation and distinguish direct unknown receipts from durable reservations.")
replace("docs/DEVELOPMENT_STATUS.md",
        "| 用量与适配 | 成功与错误透传已知/未知物理回执；payer 一次增量、subscriber 零新增与关联用量；MCP 只映射，不计算业务规则 |",
        "| 用量与适配 | 有执行详情时，成功与错误透传已知/未知用量；payer 一次增量、subscriber 零新增与关联用量；MCP 只映射，不计算业务规则 |",
        "Limit the summary receipt claim to errors/results that actually carry execution details.")
replace("docs/DIRECT_SHARED_READS.md",
        "HTTP 失败信封继续保留原错误码、消息和 HTTP 状态，并在详情存在时输出这两个字段；前置校验或其他没有执行详情的旧错误不制造收据。",
        "HTTP 失败信封继续保留原错误码、消息和 HTTP 状态，并在详情存在时输出这两个字段；前置校验或其他没有执行详情的旧错误不制造收据。尤其外层 `Service::dispatch` 在读取返回后还会终检主体有效性，该检查失败时仍可能没有 `Error.details`。因此完整 HTTP 失败面并不保证每次都能取回已发生的用量，这一剩余边界不能解释成零消耗或安全自动重发。",
        "Record the outer Service postcheck receipt gap as an explicit remaining boundary.")
replace("docs/implementation_status.json",
        "Per-process attempt receipt on success/error; payer actual or null unknown, subscriber zero incremental usage plus nonadditive physical association; no durable direct-read ledger.",
        "Direct network success and wrapped execution errors carry per-process attempt details; prechecks and the outer Service principal postcheck can omit details. Payer usage is actual or null unknown; subscriber usage remains known zero plus nonadditive physical association, which may be unknown. No durable direct-read ledger.",
        "Align the current scope summary with the actual error boundary and subscriber-known semantics; preserve the features array.")


def features_bytes(text):
    start = text.index('"features":') + len('"features":')
    while text[start].isspace():
        start += 1
    _, used = json.JSONDecoder().raw_decode(text[start:])
    return text[start:start + used].encode()


feature_before = features_bytes(before["docs/implementation_status.json"])
feature_after = features_bytes(after["docs/implementation_status.json"])
assert feature_before == feature_after
patch = "".join("".join(difflib.unified_diff(before[p].splitlines(keepends=True), after[p].splitlines(keepends=True), fromfile="a/" + p, tofile="b/" + p)) for p in before)
patch_path = out / "final-docs.patch"
assert not patch_path.exists(), "Keep the original proposal; use a new evidence filename for revisions."
patch_path.write_text(patch)
command = ["git", "apply", "--check", "--", str(patch_path)]
result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
(out / "final-docs-patch-check.log").write_text(result.stdout + result.stderr)
assert all((root / p).read_text() == text for p, text in before.items())
record = {
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "applied": False,
    "scope": "Documentation-only proposal; production, tests and all frozen documentation were left unchanged.",
    "generator_command": ["python", str(Path(__file__).relative_to(root))],
    "patch_check_command": command,
    "patch_check_exit_code": result.returncode,
    "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
    "features_bytes_preserved": True,
    "features_sha256": hashlib.sha256(feature_before).hexdigest(),
    "files": [{"path": p, "frozen_sha256": hashlib.sha256(before[p].encode()).hexdigest(), "proposed_sha256": hashlib.sha256(after[p].encode()).hexdigest()} for p in before],
    "changes": changes,
}
(out / "final-docs-proposal.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"patch": str(patch_path.relative_to(root)), "files": len(before), "edits": len(changes), "patch_check_exit_code": result.returncode, "applied": False, "patch_sha256": record["patch_sha256"]}, ensure_ascii=False))
assert result.returncode == 0
