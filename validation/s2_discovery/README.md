# 0.1.3 S2 robots 与有界站点发现验证

本批从已交付 0.1.2 `434a9dfa8ce26b86bf765c9d863c8607c3dabae0` 接续。完整机器报告见 [test_report.json](test_report.json)，来源树的 SHA-256 清单见 [source_manifest.json](source_manifest.json)，跨作者局部复核见 [independent_review.json](independent_review.json)。旧版验证目录保持原样。

## 最终本地结果

| 套件 | 实际结果 |
|---|---|
| CTest | 8/8 通过，0 失败 |
| 原生 core / batch / map | 81 组 / 18 组 / 24 组；分别 713 / 439 / 462 断言 |
| 原生 contracts | 55 组、269 断言，含 34 个保留设计 fixture |
| HTTP / CLI | 20 项通过 |
| 原 web E2E / 新 map E2E | 13 项 / 9 项通过；真实核心、Worker、CLI、MCP 进程及合成回环站点 |
| Python Worker / MCP / 配置 / 探测回放 | 541 项通过 |
| ASan / UBSan / 泄漏 | core、batch、map、contracts 四个原生可执行文件通过 |
| 设计合同参考 / 规划工具回归 | 34 项 / 13 项通过，规划仍为 83 包、161 映射、10 切片 |
| 启动器生命周期 | 7 个真实命令、17 项检查通过；两端使用同一空站点文件，结束后进程和端口关闭 |
| Python 依赖与本地包元数据 | 0.1.3 本地安装及 pip check 通过，无依赖下载 |

这些套件互有覆盖，不能相加成为产品完成度。32 个工作包部分实现、51 个未开始、0 个完整签收；S0/S1/S2 均未签署完成，完整 161 项验收没有执行。

## 日志与源码绑定

`ctest-final.log` 是最终运行，`ctest.log` 是修正 launcher 参数覆盖之前的中间通过记录（当时 533 Python）。后续新增 8 个启动前拒绝用例，最终为 541。每个执行日志有相邻的 `*-command.json`，保存精确 argv、工作目录、开始时间、退出码和耗时。四套 sanitizer 日志单独保存环境开关，不能据此声称 Python 或 HTTP 程序也被 sanitizer 检测。

`launcher_smoke.py` / `launcher_smoke.json` 保存本批可重现的独立 Linux 生命周期检查及结果，只有临时配置中的本次进程会被停止；记录不含生成的凭据。`environment.json` 记录实际工具和依赖版本。本地源码清单不包含 validation 目录；报告单独保存本目录证据哈希，二者由包含它们的 Git 提交共同绑定，避免自身哈希循环。

## 范围与限制

本批测试只向明确映射的合成 `.test` 站点发送产品 HTTP 请求。没有新增真实互联网来源或真实站点探测，也没有模型调用、付费调用或放宽生产出站规则。0.1.2 的云端两次 DNS 失败只在原目录中保留，0 HTTP 响应不能成为真实来源可用性或质量样本。

发现链路覆盖 Sitemap/index、RSS/Atom、HTML/XHTML 与 owner 历史，候选不生成本次正文快照或索引。robots 的固定版本策略、有效缓存、真实派发前检查、预算/范围/租约和删除屏障有专门回归。完整正文 frontier、公开索引声明、人工 qrels/评分、流式断线重取、独立撤销日志、安全恢复、系统解析沙箱、长时/断电与完整 Windows Worker 打包仍待后续。

发布后须查询最终提交自己的 Linux/Windows Actions。Linux 门槛包含八套普通测试、四套 native sanitizers 和规划检查；Windows 保留并新增 core/batch/map/contracts/HTTP-CLI 的阻塞门槛。本报告生成在提交前，不预写未来提交 SHA 或未来 CI 成功。
