# GitHub 交付与两条开发历史

更新：2026-10-18。用户授权接手、继续开发并推送的目标为 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。旧文档中的其他所有者路径只是历史，不是本轮目标。

## 本轮来源

| 来源 | 接收时提交 | 说明 |
|---|---|---|
| 已交付 main | `95710ba30d9495645de0e0966746446c0e70cdc9` | 0.0.2 修复、原生合同、平台回归及交付证据 |
| Claude S1 bundle | `2b121fcc6bb47973a4315b6684d429651626025c` | 原分支 `dev/s1-hybrid-workers` 的混合架构候选增量 |

本轮工作分支 `agent/s1-integration-20261018`，阶段 **0.1.1 S1 review candidate**。ZIP/bundle 哈希与逐 blob 核对见 [接收审查](handoff/CLAUDE_S1_REVIEW_20261018.md)。整合不覆盖未知远端改动，不拿旧 bundle SHA 冒充当前远端 HEAD。

交付须先读取远端分支和预期旧 SHA，提交源码及证据后复核实际 commit/tree，并让本地副本对应相同提交。是否已经推送、最终 SHA 与 CI 状态按实际交付记录判断，本文件不预写成功。

## 原始历史为何不同

2026-10-17 首次接手时，目标仓库经授权连接和 Git 协议核验为空。终端 HTTPS 写入没有可用凭据，通过已授权 GitHub connector 初始化远端，因此形成新的提交历史。原始两次提交不是该初始化提交的父历史：

- `7266ce42c64c0e5d273448d6fa49274ac02e5359`：原 0.0.1 离线实现。
- `b801b08db7b9d56108d8ea516923075007ab99f2`：原失败推送说明。

原 bundle 在用户交接包和本地历史中保留，Claude S1 沿原历史发展。本轮处理两条来源的三方差异，不按版本名选择整份目录；0.0.2 的原生合同、修复和证据继续保留。

## 证据边界

`validation/delivery_status.json` 和 `validation/test_report.json` 描述 0.0.1；`validation/takeover/` 绑定 0.0.2；`validation/s1_test_report.json` 为 Claude 的 0.1.0 随包声明。本轮真实验证集中于 [validation/s1_review/test_report.json](../validation/s1_review/test_report.json)。

CI 以最终提交对应的 [Actions](https://github.com/wmqfl861/bzs861/actions) 为准。固定依赖引用、工作流存在、本地测试和远端成功分别核对。公开仓库不默认包含私人聊天、令牌、数据库、用户探测的未审原始内容或外层 bundle。
