# S2 五阶段验证记录

当前为 **0.1.8 / schema11** 五阶段整合：[最终报告](final-report.json)、[164个冻结执行输入](final-v1-execution-inputs.json)、[源码与文档清单](final-source-manifest.json)、[范围复核](final-review.json)。原始阶段记录保持，以下计数均来自本轮实际命令。

## 最终整合结果

| 门槛 | 实际结果 |
| --- | --- |
| Linux完整CTest | 15/15套通过，0失败、0跳过；81.67秒 |
| Python Worker/MCP等 | 838项通过，包含于上述CTest |
| 原生ASan/UBSan | 9/9个测试程序通过；detect_leaks=1，halt_on_error=1 |
| 原设计合同 | 34/34通过 |
| 规划守卫 | 13/13通过；83包、10切片、161映射及原语义保留 |
| Python依赖 | pip check通过 |
| 真实旧库升级复核 | 原有2个schema8数据库场景通过最终schema11；32准备+87候选断言，不作为新增覆盖重复计数 |

18个成功命令均绑定冻结输入、实际argv/环境、日志和执行前后二进制SHA-256。普通构建与ASan构建的产物身份和实际运行一致。回环HTTP、进程和合成来源覆盖不代表真实来源可用性或人工质量；作者、独立局部复核和最终整合执行不能互相替代。

## 证据范围与保留

- stage2-author / stage2-review / stage2-adapters：终态回收、真实旧库和CLI/MCP映射证据；[最终升级复核](final-upgrade/final-upgrade-review.json)另外绑定最终集成库。
- stage3-author / stage3-baseline：队列与trap的作者执行、真实旧库迁移和基线反例；候选阶段schema号与最终schema10迁移层分开记录。
- stage4-author / stage4-review：固定候选数学/身份门控和原黄金投影修复；独立复核为只读，没有伪称重跑作者207项或产生人类质量事实。
- stage5-review / stage5-baseline：实际失败及修复后的会话、MCP、URL链和形成版本前已存在的限制复核；最终20项原生会话和14项web进程测试来自本轮统一运行。
- final-environment与环境恢复记录：执行环境重启后CMake缺失、Ubuntu快照端点502及恢复过程。两个初始配置尝试未创建进程；使用临时官方签名软件源恢复依赖，实际版本单独记录。
- final-v1-executable-restoration与crawl-executable-mode：恢复产物权限导致batch/crawl初次未创建进程，原空日志保留；仅恢复生成文件执行权限，二进制字节未变，成功运行使用新的记录名。

第一检查点0.1.6已发布为 `27966ffe61c16964c45d595e3ee2689115815217`，[该提交CI](https://github.com/wmqfl861/bzs861/actions/runs/39431992846)的Linux、Windows均成功，见[发布后观察](stage1-published-ci.json)。最终0.1.8报告由包含它的Git提交绑定；发布后必须查询该精确提交的双平台CI，不在预发布报告中预写未来结果或自身提交SHA。

原40条黄金查询/20开发15回归5留出、来源配置、原需求/蓝图和依赖锁文件字节保持。正式规划仍为36 partial、47 not_started、0 accepted；本轮未签收任何完整工作包、切片或161项原验收。真实来源S0、真实人工标签/质量、完整条件刷新、公共集合、独立撤销日志与安全恢复连续性仍待原规则验收。

## 0.1.6 历史检查点

以下原记录只对0.1.6对应输入有效。其源码/文档清单应在上述历史提交查看，不能将其中哈希套用于0.1.8工作树。原报告、命令、日志和复核文件没有改写；本README作为当前总入口更新。

该历史检查点为0.1.6：[报告](stage1-report.json)、[v2冻结执行输入](stage1-v2-execution-inputs.json)、[v2独立复核摘要](stage1-v2-review.json)。后续阶段使用新命名记录，已执行命令与日志保持不变。

修复后的最终门槛为12套CTest、6套ASan/UBSan、34项原设计合同和13项规划守卫，全部通过，0跳过。独立复核17个公开API竞态、2个真实schema7在途升级和6个旧Worker不完整回包，共25例通过。0.1.5原始7个缺陷复现保留。

最初候选曾通过其时已知门槛，后续独立复核发现旧Worker截断/206/500回包的完整性缺口。该候选在stage1-initial-report.json明确标记被取代，原输入、命令、日志和实际失败仍保留；该检查点报告只引用修复后重新冻结的stage1-v2记录。stage1-source-manifest.json和stage1-review.json属于最初候选，不能当作后续整合代码的最终身份。

最初stage1-asan-*.log中不带final的6个记录是沙箱ptrace导致LeakSanitizer不能工作的环境失败；未关闭检测、未更改当时二进制，同输入重跑通过。最终v2同样开启detect_leaks=1并通过。

执行前冻结代码、构建、测试和配置输入，每个命令记录SHA及前后身份。报告由包含它的Git提交绑定，发布后另核对该精确提交Linux/Windows CI。回环测试不代表真实来源S0、人工质量或完整83工作包/161验收签收。旧schema头文件可从2007690c提取；日志记录其SHA，临时数据库和二进制不公开。
