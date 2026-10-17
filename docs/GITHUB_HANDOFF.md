# GitHub 交付与历史来源

当前目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。接手记录：[TAKEOVER_20261017.md](handoff/TAKEOVER_20261017.md)。

2026-10-17 接手时，通过 GitHub 插件读取到仓库为 public、默认分支 main、当前连接具备 push 权限，分支列表为空；通过 Git 协议复核也没有远程引用。这只描述当时基线，后续操作仍须重新核对。

终端的 GitHub HTTPS 写入没有配置凭据，dry-run 返回 `could not read Username`。因此交付使用用户已授权的 GitHub 连接：初始化默认分支，再写入完整 Git tree、commit，并以预期旧 SHA 和非强制方式更新 main。不会从其他项目提取密钥或修改保护规则。

这条路径产生新的远程提交 ID；原 bundle 的两次提交并非远端新提交的父历史：

- `7266ce42c64c0e5d273448d6fa49274ac02e5359`：原离线实现。
- `b801b08db7b9d56108d8ea516923075007ab99f2`：原失败推送说明。

原始历史保留在用户交接包的 `03_git_history/bzs861_bootstrap_v0.0.1.bundle`，本地也恢复在独立分支。上传的是已审核的源码、计划、文档、测试与本轮验证记录，不包含外层私人聊天、运行凭据或数据库。

提交后应读取 GitHub main 的真实 commit/tree，并把本地工作副本检出为相同 commit。上传前的本地 tree 与远端 tree 必须完全一致；不能只以本地 commit 存在声称完成交付。GitHub CI 以该提交对应的 Actions 结果为准。

`validation/delivery_status.json` 保留原 0.0.1 对旧目标仓库的失败尝试；它不是当前仓库的交付状态。当前本地源码与构建证据由 `validation/takeover/test_report.json` 及源码 SHA-256 绑定。
