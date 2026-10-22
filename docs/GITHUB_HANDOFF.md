# GitHub 交付与开发历史

更新：2026-10-21。用户授权修复、继续开发并适时推送的唯一目标为 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。旧文档中的其他所有者路径是历史，不能作为本轮推送目标。

## 当前增量与已交付输入

当前范围为 **0.1.9 S2 五个接续增量**。本轮起点是已发布0.1.8 `2c536e9834c4897b0c70e3bd6651b765cc67f45e`，tree `522c5d78fdb9e935bad976e561d92c74f20b7a12`；其 [CI 39435330847](https://github.com/wmqfl861/bzs861/actions/runs/39435330847) 已核对Linux/Windows成功。该成绩只证明基线。本轮范围见 [接续计划](handoff/S2_NEXT_FIVE_20261021.md) 与 [当前报告](../validation/s2_next_five/final-report.json)，发布前报告不预写未来自身SHA或CI成功。

| 已交付输入 / 历史来源 | 提交 | 说明 |
|---|---|---|
| 0.1.8 main | `2c536e9834c4897b0c70e3bd6651b765cc67f45e` | 上一轮五阶段集成，schema11；本轮真实输入基线 |
| 0.1.6 第一检查点 | `27966ffe61c16964c45d595e3ee2689115815217` | 读取顺序屏障、旧Worker完整性与独立复核；历史第一阶段检查点 |
| 0.1.5 main | `2007690c9403b8ae593101488de1adf228608284` | 持久发布约束、抓取陷阱与真实进程恢复回归 |
| 0.1.4 main | `90ca7e93f9c96a43ff8d99b237638e4aa2aa2c63` | 有界正文前沿、页预算、链接边、页面发布规则；tree `b51e0f126c49727506ad2cc08ff3912d59108703` |
| 0.1.3 main | `8a4e0f4cc630627ffa91c49598c66fec7fb4291d` | 版本化 robots 准入、独立站点登记、持久有界发现、每跳预算与配置身份 |
| 0.1.2 main | `434a9dfa8ce26b86bf765c9d863c8607c3dabae0` | 可恢复 probe、黄金冻结、离线 replay、持久显式批量读取 |
| 0.1.1 main | `426c431fd3b39390564045e31316e79ca3f27483` | 审查并整合 Claude S1 混合架构；保留先前原生修复和合同 |
| 0.0.2 main | `95710ba30d9495645de0e0966746446c0e70cdc9` | 原生配置合同、修复及平台回归 |
| 原 Claude S1 bundle | `2b121fcc6bb47973a4315b6684d429651626025c` | 原 `dev/s1-hybrid-workers` 候选输入；不是当前 HEAD |

0.1.4 的 [CI 39207218575](https://github.com/wmqfl861/bzs861/actions/runs/39207218575)、0.1.3 的 [CI 39125426011](https://github.com/wmqfl861/bzs861/actions/runs/39125426011) 与 0.1.2 的 [CI 39113748316](https://github.com/wmqfl861/bzs861/actions/runs/39113748316) 在各自交付时核对 Linux/Windows 成功，它们只证明对应历史提交；后续版本必须查询最终发布提交自己的工作流。Claude ZIP/bundle 和逐 blob 核对见 [接收审查](handoff/CLAUDE_S1_REVIEW_20261018.md)。

交付前读取远端 main 与预期旧 SHA，先完成代码、复核和实际测试，再以非强制更新发布。提交的 tree 必须与本地已验证内容相同；远端前进时先检查新差异，不覆盖未知修改。发布后获取实际 commit/tree，将本地副本对齐，并核对对应 Linux/Windows 的实际步骤和结论。

当前报告由包含它的 Git 提交绑定，不预写未来自身 SHA 或未来 CI 成功。最终 SHA 与 CI 以实际发布记录为准。

## 原始历史为何不同

2026-10-17 首次接手时，目标仓库经授权连接和 Git 协议核验为空。通过已授权 GitHub 连接初始化远端，因此形成新的提交历史。原始两次提交不是该初始化提交的父历史：

- `7266ce42c64c0e5d273448d6fa49274ac02e5359`：原 0.0.1 离线实现。
- `b801b08db7b9d56108d8ea516923075007ab99f2`：原失败推送说明。

原 bundle 在交接材料中保留，Claude S1 沿原历史发展。0.1.1 已处理两条来源的三方差异；后续增量从已整合 main 接续，不再次用旧 bundle 替换整个目录。

## 证据分期

`validation/test_report.json` 描述 0.0.1；`validation/takeover/` 绑定 0.0.2；`validation/s1_test_report.json` 为 Claude 0.1.0 随包声明；`validation/s1_review/` 绑定 0.1.1；`validation/s0_s2_progress/` 绑定 0.1.2；`validation/s2_discovery/` 绑定 0.1.3；`validation/s2_crawl/` 绑定 0.1.4；0.1.5 为 `validation/s2_hardening/`；0.1.8五阶段为 `validation/s2_five_stages/`；当前0.1.9接续为 `validation/s2_next_five/`。旧证据不覆盖、不改名冒充新运行。

固定依赖、工作流存在、本地通过、GitHub 实际成功分别核对。公开仓库不包含私人聊天、令牌、运行数据库、用户探测的未审原始内容或外层 bundle。
