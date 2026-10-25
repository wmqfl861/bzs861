#!/usr/bin/env python3
"""Close the independent focused review from existing immutable records only."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEBUG_SHA = 'eb4fcd85d839bd43e78382943c8d0a7c73f47c178ff528a5fcd20851cef6404f'
SERVER_SHA = 'e873a92e499a70bf1e5daf1749c9510d2380756e56c84bff86045e8be11867f8'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitizer-library-sha256', required=True)
    args = parser.parse_args()
    selected = {
        'candidate-native-v3': (13, 551, DEBUG_SHA),
        'candidate-extra-v3': (3, 99, DEBUG_SHA),
        'capacity-candidate-v3': (2, 29, DEBUG_SHA),
        'candidate-wire-v3': (12, 137, SERVER_SHA),
        'candidate-native-asan-v3': (13, 551, args.sanitizer_library_sha256),
        'candidate-extra-asan-v3': (3, 99, args.sanitizer_library_sha256),
        'capacity-candidate-asan-v3': (2, 29, args.sanitizer_library_sha256),
    }
    summaries = {}
    for name, (cases, checks, binary_input) in selected.items():
        path = HERE / name / 'summary.json'
        value = json.loads(path.read_text())
        assert value['cases'] == cases and value['checks'] == checks and value['failures'] == 0, name
        assert value['inputs_unchanged'], name
        assert value.get('library_sha256', value.get('server_sha256')) == binary_input, name
        record = json.loads((HERE / name / 'run-command.json').read_text())
        assert record['exit_code'] == 0 and record['execution_record_valid'] and not record['changed_inputs'], name
        if 'asan' in name:
            overrides = record['environment_overrides']
            assert overrides['ASAN_OPTIONS'] == 'detect_leaks=1:halt_on_error=1', name
            assert overrides['UBSAN_OPTIONS'] == 'halt_on_error=1:print_stacktrace=1', name
        summaries[name] = {'path': name + '/summary.json', 'summary_sha256': digest(path), **value}

    audit_command = [sys.executable, str(HERE / 'verify_independent_records_v2.py'), '--output', 'records-final-audit.json']
    audit_result = subprocess.run(audit_command, cwd=REPO, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with (HERE / 'records-final-audit.log').open('xb') as stream: stream.write(audit_result.stdout)
    assert audit_result.returncode == 0, 'Archived record audit failed'
    archive_audit = json.loads((HERE / 'records-final-audit.json').read_text())
    assert archive_audit['archive_integrity_passed']

    # Recheck only the immutable copied dependencies of old runs. Current
    # production files legitimately differ across versions and are not silently
    # compared against a superseded candidate's captured source hash.
    immutable_prefixes = [str(HERE) + '/', '/tmp/bzs861-direct-independent-20261024/handoff-audit/',
                          str(REPO.parent / 'direct-shared-baseline-20261024') + '/']
    frozen_checked: dict[str, str] = {}
    for folder in sorted(path for path in HERE.iterdir() if path.is_dir()):
        for command in sorted(folder.glob('*-command.json')):
            value = json.loads(command.read_text())
            for name, expected in value.get('inputs_after', {}).items():
                if not any(name.startswith(prefix) for prefix in immutable_prefixes): continue
                path = Path(name)
                assert path.is_file() and digest(path) == expected, name
                assert name not in frozen_checked or frozen_checked[name] == expected, name
                frozen_checked[name] = expected

    frozen = json.loads((HERE / 'candidate-native-v3/frozen-inputs.json').read_text())
    production = frozen['production_inputs']
    for path, expected in production.items(): assert digest(Path(path)) == expected, path
    for name in ('candidate-extra-v3', 'candidate-native-asan-v3', 'candidate-extra-asan-v3'):
        value = json.loads((HERE / name / 'frozen-inputs.json').read_text())
        assert value['production_inputs'] == production, name
    source_binding = json.loads((HERE / 'source-review-v3/binding.json').read_text())
    for name, expected in source_binding['files'].items():
        assert digest(HERE / 'source-review-v3' / name) == expected
        assert digest(REPO / name) == expected

    build_path = HERE.parent / 'build/candidate-v3-command.json'
    build = json.loads(build_path.read_text())
    assert build['execution_record_valid'] and build['exit_code'] == 0 and not build['changed_inputs']
    assert build['binaries_after']['build/libbzs_core.a'] == DEBUG_SHA
    assert build['binaries_after']['build/searchd'] == SERVER_SHA
    assert digest(build_path.parent / build['log']) == build['log_sha256']
    build_inputs = json.loads((build_path.parent / build['inputs']).read_text())['files']
    for name, expected in production.items():
        assert build_inputs[str(Path(name).relative_to(REPO))] == expected, name

    history = {}
    for name in ('baseline-v1', 'candidate-native-v1', 'candidate-native-v1-oracle-corrected', 'capacity-baseline-v1', 'capacity-candidate-v2'):
        path = HERE / name / 'summary.json'
        history[name] = {'path': name + '/summary.json', 'sha256': digest(path), **json.loads(path.read_text())}
    report = {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'passed_final_v3_independent_focused_review',
        'finalizer_sha256': digest(Path(__file__).resolve()),
        'baseline_commit': '346048b4ae329e750d15f1e35952aab4513e3912',
        'baseline_tree': '458a6f321e94defd74616ba6ebbf2983252cb495',
        'final_debug_library_sha256': DEBUG_SHA,
        'final_sanitizer_library_sha256': args.sanitizer_library_sha256,
        'final_searchd_sha256': SERVER_SHA,
        'selected_records': summaries,
        'counts': {
            'native_distinct_cases': 18, 'native_checks_per_final_build': 679,
            'sanitizer_replay_cases': 18, 'sanitizer_replay_checks': 679,
            'wire_and_adapter_cases': 12, 'wire_and_adapter_checks': 137,
            'new_blueprint_acceptance_entries': 0,
            'rule': '13 primary + 3 supplemental + 2 capacity native cases. Sanitizers replay the same cases; wire checks are a separate transport layer. Historical failures, baseline observations and repetitions are never added as new product acceptance.'
        },
        'verified_bindings': {
            'archive_groups': len(archive_audit['groups']),
            'archive_audit': 'records-final-audit.json', 'archive_audit_sha256': digest(HERE / 'records-final-audit.json'),
            'current_copied_inputs_checked': len(frozen_checked),
            'current_copied_inputs': frozen_checked,
            'production_inputs': production,
            'all_final_native_production_inputs_equal': True,
            'root_debug_build_record': '../build/candidate-v3-command.json',
            'root_debug_build_record_sha256': digest(build_path),
            'root_debug_build_binary_and_source_binding_matches': True,
            'source_capture': 'source-review-v3/binding.json',
            'source_capture_sha256': digest(HERE / 'source-review-v3/binding.json'),
        },
        'retained_history': history,
        'failure_classification': [
            'Released baseline executes two physical calls for synchronized same-owner refresh and omits Worker-provided direct usage; this establishes missing capability, not a baseline regression.',
            'The first v1 reviewer source incorrectly used historical snapshot.indexed for current search publication in three cases. Its four-failure source/log remain intact. A separate corrected public-search oracle on the same original library leaves the real malformed-usage restriction failure. Expectations were not changed to excuse the defect.',
            'V2 correctly retains independent valid restrictive observations after invalid receipt/profile proof, but the newly aggregated snapshot JSON parser imposed a new byte limit after commit. The same 700 KiB U+0001 source passes released baseline and fails both v2 paths with E_JSON_LIMIT. Those failed observations remain unchanged.',
            'V3 freezes only nine fields needed by window, each as a separate immutable string, preserving full metadata. Same-source legacy and shared capacity cases now pass, as do all original boundaries and transport checks. External input limits and Json API remain unchanged.',
            'The author separately diagnosed the v1 Error-default-Json temporary deadlock in the unchanged core test. This independent review inspected the separate constructor fix but does not claim an independent reproduction of that hang.',
            'A first read-only archive verifier failed on baseline binary versus newer path metadata names. Its original source and diagnostic are preserved; v2 verifier accepts both. This was not product execution.'
        ],
        'source_review': {
            'conclusion': 'No remaining blocker identified for the documented same-WebService direct-read increment after the v3 capacity repair.',
            'reviewed_boundaries': [
                'Exact owner, physical request, complete authorization/deletion/publication fence, full captured metadata and current profile admission identity.',
                '32 cumulative members, 64 open groups, same-thread independent fallback, closure identity and steady-clock subscriber wait.',
                'No Worker or wait under coordination lock/Store transaction; immutable field map/error/usage published with the finished mutex transition.',
                'Single body/304 commit and caller-specific return transaction: epoch, original deletion generation, all actual aliases, full metadata hash and current/historical publication projections.',
                'Invalid usage or profile cannot bypass independently valid cache invalidation and restrictive observations. Invalid body remains uncommitted.',
                'Fixed payer; known versus unknown physical receipt; subscriber zero increment; caller-window errors; Worker-forged receipt details; HTTP/CoreClient/MCP preservation.',
                'The nine field names cover every snapshot field window consumes; canonical body is read from Store. Per-field parser limits retain already validated Store field boundaries.',
                'Legacy fake Worker changes reject optional profile calls before consuming fetch counters. No existing substantive assertion was relaxed to accept the new behavior.'
            ],
            'known_remaining_boundary': 'The unchanged outer Service::dispatch can reject after WebService::read returns if revocation commits in that later interval. That error is outside the direct-read receipt wrapper; documentation limits the receipt guarantee accordingly. This is not a claim of end-to-end receipt preservation for every outer error.'
        },
        'scope_limits': [
            'Local independent focused evidence only; root full CTest/sanitizer/auxiliary report requires its own final review.',
            'No current remote CI, main publication, real source/site permission, restart-safe direct ledger, cross-process/service/durable/search/crawl sharing or public direct-cancellation claim.',
            'No full B-24/CACHE-01 acceptance; original blueprint package/acceptance/slice totals and fixed decisions remain outside this focused increment.',
            'Runtime binaries, temporary SQLite fixtures and local authentication material are not archive deliverables; text/source/header/log/manifests are retained.'
        ],
    }
    # Stable top-level aliases for the parent report assembler.
    report['production_inputs'] = production
    report['limits'] = report['scope_limits']
    save(HERE / 'independent-review.json', report)

    readme = '''# 直接读取共享：独立核验

最终候选 v3 的独立检查通过。完整机器报告见 [independent-review.json](independent-review.json)，根整合门禁仍以父目录的最终报告为准。本目录没有修改生产、正式测试、旧 validation 或远端。

## 最终执行与精确身份

| 记录 | 场景 / 检查 | 结果 |
| --- | --- | --- |
| [主要原生边界](candidate-native-v3/summary.json) | 13 / 551 | 通过 |
| [补充原生边界](candidate-extra-v3/summary.json) | 3 / 99 | 通过 |
| [容量回归](capacity-candidate-v3/summary.json) | 2 / 29 | 通过 |
| [实际 HTTP、CoreClient、MCP](candidate-wire-v3/summary.json) | 12 / 137 | 通过 |
| [主要 ASan/UBSan 重放](candidate-native-asan-v3/summary.json) | 13 / 551 | 通过 |
| [补充 ASan/UBSan 重放](candidate-extra-asan-v3/summary.json) | 3 / 99 | 通过 |
| [容量 ASan/UBSan 重放](capacity-candidate-asan-v3/summary.json) | 2 / 29 | 通过 |

原生共有 18 个独立场景、每个最终构建 679 次检查。Sanitizer 使用 `detect_leaks=1:halt_on_error=1` 和 `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`，重放相同场景；12 个 wire 场景属于另一验证层。执行次数、旧版本复验和失败诊断均不增加蓝图验收项。

Debug 静态库 SHA-256 为 `''' + DEBUG_SHA + '''`，实际 searchd 为 `''' + SERVER_SHA + '''`。Sanitizer 库 SHA-256 为 `''' + args.sanitizer_library_sha256 + '''`。逐条命令、输入前后摘要、实际日志字节和编译链接身份保存在各记录目录；最终源码另存于 [source-review-v3](source-review-v3/binding.json)。所有最终运行输入在执行期保持不变。

## 验证覆盖

主要原生检查包括：共享 profile 变化仍实施缓存失效和有效 noindex 观察；304 只提交一次且绑定完整 metadata；成员交付前原 URL、最终 URL、中间 URL 的有限别名删除后复活；主体撤权；付款与订阅窗口错误隔离；未知 Worker 错误不重发；畸形用量不能跳过有效限制。

补充检查覆盖畸形实际 profile 与单独有效观察、Worker 伪造错误收据不能覆盖 Core 归属，以及订阅者在受控 Worker 迟迟不返回时于自己的等待边界退出。超时者只报告已知零增量与未知物理用量；后续付款成功不会改写已返回的错误收据。

Wire 检查使用实际冻结 searchd、真实 HTTP 调用、CoreClient 和 MCP 工具方法，并连接本机认证 Worker fixture；没有 mock 掉 `_call`，没有访问真实来源。它核对成功、缓存命中、已知失败、窗口错误、no-store、旧 Worker 无用量和未知 RPC 失败的字段传递。Wire 用例本身不证明并发合并，原生用例承担该证明。

## 原始失败与修正链

已发布基线 `346048b4` 的公开接口反例见 [baseline-v1](baseline-v1/summary.json)：两个受同步闸门控制的同 owner refresh 发出两次物理 fetch，两个成功响应都遗漏了 Worker 提供的直接读取用量。这是缺少能力的观察，不是基线产品回归。旧头文件和已验证静态库来源见 [baseline-source-provenance.json](baseline-source-provenance.json)。

首份独立 v1 程序保留了四个失败。前三项错误地把不可变历史 `snapshot.indexed` 当作当前搜索可见性；其源码和日志均未重写。[分类记录](v1-failure-classification.json)解释这一审阅 oracle 错误。另一个独立文件改用公开 `Store.search` 后，仍以同一 v1 库复验，剩下真实的“畸形用量抢先抛错，跳过有效 no-store/noindex”回归，见 [纠正 oracle 的原候选重放](candidate-native-v1-oracle-corrected/summary.json)。正确的限制期待没有被改成允许泄漏。

v2 修复了用量/profile 早退路径，原 13+3 和 wire12 均通过。随后源码审核发现新的整体快照复制上限。新增同一旧公开接口容量程序，在真实已发布基线的 legacy 单读和有效 profile 双读均成功；v2 两项均失败为 `E_JSON_LIMIT`，尽管 716,800 字节正文已保存。完整快照因 JSON 转义及重复 publication 文本达到 8,603,131 / 8,603,477 字节，见 [baseline 容量正例](capacity-baseline-v1/summary.json)和 [v2 原失败](capacity-candidate-v2/summary.json)。[阶段报告](review-through-v2.json)明确没有把 v2 作为最终可交付候选。

v3 只冻结窗口需要的九个字段，逐字段独立编码和解析，完整 metadata 与读取 guard 保留；不再复制未使用的 canonical_text/publication 聚合，不放大外部协议限制，也不修改 Json API。相同容量源码和原全部期望现通过 Debug 与 sanitizer。原 13+3 与 wire12 也按最终二进制重放，确认没有遗漏窗口或收据字段。

作者另行诊断的 Error 默认 Json 临时对象死锁位于父目录 focus 记录；本审阅检查了分离三参数/四参数构造函数的修复，不声称独立复现那次 core 挂起。只读归档核验器也曾因旧记录用 `binary`、新记录用 `path` 键而中止；[诊断](verifier-diagnostic-v1/diagnostic.json)及原核验源码保留，修正版兼容两种记录。这不是产品测试失败。

## 审核结论与界限

源码审核未发现阻塞本次同一 WebService 内直接读取增量的剩余问题。成员登记、关闭和等待不跨 Worker/Store 锁；付款归属固定；成功只提交一次；各成员在最终 Store 事务中分别核对权限、metadata、全部实际别名和发布历史。九字段 map 在完成标志的互斥发布后只读，每个调用得到自己的 Json 对象。

明确保留一个已有外层边界：`Service::dispatch` 可在 `WebService::read` 返回后再因撤权拒绝，这一极晚错误位于直接读取收据包装之外。文档把收据保证限定为该路径实际包装的失败。本报告不将其扩大为所有外层错误都保存收据的保证。

本目录只证明有限本地场景。未声明当前远端 CI 或 main 发布、真实网站批准、跨服务/进程或 direct/read_many 混合协调、公开 direct 单成员取消、可恢复直接执行账本，亦未签收完整 B-24/CACHE-01。临时二进制、SQLite 夹具与认证材料不作为归档交付物；完整文本、源码、冻结头文件和原日志均在清单中。

[最终只读完整性核对](records-final-audit.json)逐一校验所有已关闭记录；失败记录保持失败。最终归档清单见 [archive-manifest.json](archive-manifest.json)，它不包含自身，之后的根整合报告独立复核可再追加单独清单。
'''
    with (HERE / 'README.md').open('x', encoding='utf-8', newline='\n') as stream: stream.write(readme)
    files = {}
    excluded = []
    for path in sorted(HERE.rglob('*')):
        if not path.is_file(): continue
        relative = path.relative_to(HERE)
        if 'runtime' in relative.parts or '__pycache__' in relative.parts or path.suffix in ('.pyc', '.a', '.sqlite', '.sqlite-wal', '.sqlite-shm'):
            excluded.append(str(relative)); continue
        path.read_text(encoding='utf-8')
        files[str(relative)] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    save(HERE / 'archive-manifest.json', {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Independent text/source/header/log evidence through focused v3 closure; excludes this manifest itself and generated runtime state. Later root final-report review is append-only supplemental evidence.',
        'file_count': len(files), 'files': files, 'excluded_runtime_paths': excluded,
    })
    print(json.dumps({'status': report['status'], 'report': str(HERE / 'independent-review.json'),
                      'native_cases': 18, 'native_checks': 679, 'wire_cases': 12, 'wire_checks': 137,
                      'archive_file_count': len(files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
