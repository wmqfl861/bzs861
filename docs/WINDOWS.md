# Windows 状态与后续移植

阶段：0.1.1 S1 review candidate。v1.2 允许 Linux 先行、Windows 安装包在 S9 交付，但已经交付的 Windows 核心、原生合同和 HTTP/CLI 门槛继续保留。

## 当前验收方式

本轮 Windows 成绩必须读取最终提交的 [GitHub Actions](https://github.com/wmqfl861/bzs861/actions)。工作流使用固定 Action/vcpkg 引用并检查命令退出码，Windows 任务不能用 `continue-on-error` 将失败变成发布成功。0.0.2 运行结果属于历史，Claude S1 包也明确未执行 Windows；两者都不能替代整合后实际运行。

Python worker/MCP 的新增平台覆盖按实际工作流和报告单独记录，不因为已有核心可编译就宣称全部网络链可用。日志、失败与未执行项见 [本轮报告](../validation/s1_review/test_report.json)。

## 保留的移植约束

- C++20、CMake、标准文件路径和 UTF-8；MSVC 使用 `/utf-8`。
- 进程间使用回环 TCP HTTP，不引入 Unix socket、fork 或依赖特定信号的业务合同。
- 启停由 Python 启动器管理；操作系统差异不得改变来源、权限或合同语义。
- 原生日期计算在乘法前转为 64 位，保留 1901/2038 与四位年份回归；Unicode JSON 测试继续验证真实解析结果。
- 新依赖要核对 Windows 可获得性与具体发行物，不能只凭蓝图声明有 wheel 或 vcpkg port。

## 认证文件的 ACL 与最小权限令牌（CL-08）

`os.chmod`/`0o600` 在 Windows 上不生效，Windows 用 DACL 保护秘密文件：

- `bzs init`、`bzs mcp-token add`、`tools/create_auth.py`、探针证据写入在写完文件后设置受保护 DACL：断开继承，仅当前用户 SID 与 LOCAL_SYSTEM 保留完全控制（ctypes `SetNamedSecurityInfoW`，`icacls` 仅作后备写入器，无新依赖）。`BZS_HOME` 目录本身同样处理；把 `BZS_HOME` 指到既有目录会收紧该目录的 ACL（用户保持完全控制）。
- `bzs doctor` 用 `GetNamedSecurityInfoW` 逐个读 BZS_HOME、api.token、admin.token、owner.token、worker.token、auth.json、mcp_clients.json 的 DACL：Everyone/Users/Authenticated Users 可读、DACL 缺失/为 NULL 或读取失败都报错退出（fail closed：无法验证不等于健康）。POSIX 端 doctor 检查组/其他人权限位，与 core 对 auth.json 的启动检查同规则。
- 最小权限：`bzs init` 生成两个令牌——owner（`admin:false`，写入 api.token，是 MCP/api.token 的默认）和 admin（`admin:true`，写入 admin.token，仅 CLI 管理命令如 `revoke-principal` 使用）。MCP 默认令牌选择在 api.token 仍是管理员令牌且存在 owner.token（非管理员）时优先后者。
- 旧配置兼容：CL-08 之前的家目录只有一个管理员令牌（api.token + auth.json 单条目），继续被识别并照常工作；重复 `bzs init` 不会轮换令牌，只会把受保护 DACL 重新应用到已存在的秘密文件上。

## 尚需完成

优雅退出、Python 进程清理、worker/MCP 全链路、受控网络边界、升级/恢复演练和安装包仍需按对应切片完成。SQLite 的持久设置不能代替断电测试；备份在所有平台都保持隔离，不能绕过撤销连续性检查。

正式打包前执行干净安装、旧数据迁移、离线/网络适用套件、平台权限和退出回归，再签 S9 RELEASE-01。
