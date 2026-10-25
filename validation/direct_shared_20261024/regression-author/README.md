# 直接读取共享：作者输入与静态检查归档

此目录保存作者侧冻结测试源码、语法检查记录和既有测试保持比较。九份来源文件均从作者工作目录按原字节复制；其内部记录保留当时的原始路径和陈述，没有为归档改写。

**这些材料不是实际运行通过的证据。** 原生候选运行、HTTP 门禁、整合测试和 sanitizer 的命令、完整日志及输入绑定由 root 在本批 focus / final 等相应记录中保存。本次归档没有执行测试，没有复制二进制，也没有搬入或改写旧 validation。

## 输入版本

| 输入 | 范围与配套记录 |
| --- | --- |
| `direct-shared-v1-input.cpp` | 最初 20 组；`syntax-v1-result.json` 保存作者语法检查结果。 |
| `direct-shared-v2-input.cpp` | 追加无效 usage 不能跳过合法缓存与发布限制的第 21 组；`direct-shared-v2-record.json` 记录语法检查和旧 20 组保持证明。 |
| `direct-shared-v3-input.cpp` | 追加 malformed freshness 不能跳过独立 noindex 观察的第 22 组；`direct-shared-v3-record.json` 记录语法检查和旧 21 组保持证明。 |
| `integration-before-http-receipts.py` | HTTP 收据回归加入前的完整输入，用于核对既有类和断言。 |
| `integration-http-receipts-v1.py` | 新增三项真实 HTTP 收据测试后的冻结输入；`integration-http-receipts-v1-record.json` 记录静态检查与变更说明。 |

## 保持与完整性

归档时重新做了纯文件比较：v2 删除唯一新增组后逐字节等于 v1；v3 删除唯一新增组后逐字节等于 v2。因此此前全部测试、辅助代码和 main 均保持。HTTP 原 `RunningServer`、`Integration`、`HTTPExecution` 三个类的源码段逐字节相同；变化限于 Worker 测试夹具的本地 profile/可选响应支持，以及新增三项收据测试。

`manifest.json` 列出每份材料的大小、SHA256、原字节复制结果和上述比较结果；`SHA256SUMS` 另提供常规完整性清单，并包含 manifest 自身。清单不为自身生成循环哈希。
