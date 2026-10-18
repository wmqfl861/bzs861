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

## 尚需完成

Windows 认证文件 ACL、优雅退出、Python 进程清理、worker/MCP 全链路、受控网络边界、升级/恢复演练和安装包仍需按对应切片完成。SQLite 的持久设置不能代替断电测试；备份在所有平台都保持隔离，不能绕过撤销连续性检查。

正式打包前执行干净安装、旧数据迁移、离线/网络适用套件、平台权限和退出回归，再签 S9 RELEASE-01。
