# 原生配置合同（0.0.2）

`contract.validate` 用 C++ 校验与规范化三类固定 v1.1 合同。它不安装模型、读取凭据、注册来源、保存配置或启动搜索；结果中的 `applied=false` 表示配置尚未应用。

实现：[contracts.hpp](../include/bzs/contracts.hpp)、[contracts.cpp](../src/contracts.cpp)。结构基线：[模型](planning/schemas/model-config.schema.json)、[来源](planning/schemas/source-manifest.schema.json)、[搜索请求](planning/schemas/search-request.schema.json)。这不是通用 JSON Schema 引擎；修改基线 Schema 时必须同步审核原生实现与回归。

## 调用

启动服务并按 README 设置 `SEARCH_API_TOKEN` 后：

```bash
./build/searchctl validate model-config docs/planning/schemas/model-config.no-model.example.json
./build/searchctl validate source-manifest docs/planning/schemas/source-manifest.example.json
./build/searchctl validate search-request docs/planning/schemas/search-request.example.json
```

CLI 读取最多 65,536 字节的本地 JSON 文件，通过真实回环 HTTP 调用服务。HTTP 请求格式为：

```json
{
  "operation": "contract.validate",
  "params": {
    "kind": "model-config",
    "config": {
      "policy": {
        "allow_reference_backends": false,
        "external_nonmodel_services": "reviewed_free_only",
        "allow_provider_hosted_search": false,
        "allow_unconfigured_fallback": false
      },
      "default_text_profile": null,
      "profiles": {},
      "roles": {}
    }
  }
}
```

响应 `result` 包含 `kind`、`validated=true`、`applied=false` 和 `effective_config`。失败遵循原 API 错误信封。来源示例里的审核日期是设计样例；实际调用以服务端当时时间检查，日期过期就拒绝。

## 模型配置

允许空 `profiles`、空 `roles` 和 `default_text_profile=null`。这对应无模型基础部署，不影响现有离线导入和搜索。

| 角色配置 | 规范化语义 |
|---|---|
| planner、extractor、verifier、writer 缺省或 `{}` | 继承默认文本 profile；无默认时禁用 |
| 任一角色显式 `null` | 禁用该角色 |
| embedder、reranker、vision、asr 缺省 | 禁用，不自动绑定文本模型 |
| 角色显式指定 profile | 必须存在，且具备该角色所需能力 |
| 角色 overrides | 覆盖相应配置字段，但不能超过可信部署上限 |

文本角色要求 `text`；embedder 要求 `embeddings`；reranker 接受 `reranking` 或 `text`；vision 要求 `image_input`；asr 要求 `asr`。未声明能力不会由协议名或模型名推断。

原生结果补齐 profile 的 `limits`、`reasoning`，输出八个 `effective_roles` 及 `text_fallback_profiles`。缺省限额为 timeout 90,000 ms、并发 1、最大输出 4,096 token、重试 0；缺省 reasoning 为 `provider_default`。部署上限低于缺省值时降低自动生成的默认值，显式超过上限的输入报错。角色之间的覆盖不会互相污染，输入对象不被修改。

`reasoning.mode=explicit` 要求非空的 `provider_parameters` 对象。`credential_ref` 只作为引用保留，不解析环境变量或密钥存储。支持用户配置的本地或远程模型端点，仍须在未来执行层单独核验模型可用性与访问授权。

## 来源清单

检查必填字段、枚举、ID、限额和 URL。启用标志为 true 时，还要求：审核通过、无付费或试用依赖、自动化访问获准、非空审核人和逻辑服务 ID、有效条款 URL、审核时间不在未来且尚未到期。

声明有限请求配额时必须同时声明正数配额窗口。审核到期时间必须晚于检查时间。时间比较保留小数秒精度，并统一时区语义。

这些检查只能确认提交的审核元数据符合合同，无法证明审核资料真实。即使清单包含 `enabled=true` 且校验通过，服务仍不注册或启用它；真实来源审核记录、执行时授权、DNS/重定向/出站策略必须另行实现。

## 搜索请求

校验正式 v1.1 的字符串/AST、scope、硬过滤、预算及可选隐私字段。AST 最多 16 层、512 节点，每个 AND/OR 最多 64 个子节点。字符串长度按 Unicode 码点检查，字节预算另外计算。

硬过滤始终是额外 AND。同一日期字段出现多个 `after` 时取最晚下界，多个 `before` 时取最早上界；`eq` 必须彼此相同且落在所有开区间边界内。不同字段分别计算，条件顺序不改变结论。`network_mode=offline` 只允许 `scope.corpus=local`。

日期换算在天数乘秒数之前使用 64 位整数，支持四位年份 `0001..9999`；回归覆盖 Windows 的 1901/2038 整数边界和跨日时区等价性。

校验会补齐 `allow_unverified=false`。没有注册模型或来源配置，因此不会解析 `model_profile_ref`、`source_ids` 或 collection 的跨配置身份；权限、私有数据外发和实际预算消耗仍需执行层检查。

当前 `/api/v0` 的 `search` 仍是已文档化的离线子集。正式 `search-request` 通过校验，不代表可以把整个规范化对象直接传给 v0 `search` 执行，也不代表 `code`、`author` 等未索引字段可用。

## 限额与错误

服务端使用 `ContractLimits` 默认值；接口不接受调用方提交 `now_ms`、`validation_now_ms` 或 `limits`。可信 C++ 调用方可以下调部署上限，今后持久部署配置接入仍需单独实现。默认值只是校验天花板，不是吞吐、资源或费用承诺。

| 错误码 | 含义 |
|---|---|
| `E_SCHEMA` | 类型、字段、枚举、数值或结构不符合合同 |
| `E_CONTRACT` | 引用、角色能力、日期、URL 或配置语义矛盾 |
| `E_CONTRACT_LIMIT` | 超过字节/AST预算或可信部署上限 |

HTTP 总体正文上限仍为 2 MiB；合同对象在序列化后另外受 64 KiB 限制。JSON 不接受重复键、非法 UTF-8 或 NUL 配置字符串；数值遵循现有严格整数表示，不做隐式类型转换。例如 JSON 的 `1` 可作为整数，`1.0` / `1e0` 仍被拒绝；通用 JSON Schema 会把它们视为整数，因此本批没有宣称完全等价的通用 Schema 兼容性。这一边界避免先转 double 后的舍入把非整数误判为整数，后续精确十进制归一化需单独实现与验收。

## 与原 Python 基线的关系

保留 [Python 设计校验器](planning/tools/validate_contracts.py) 和原 34 组 fixture 的历史版本。新增 [原生测试](../tests/contracts.cpp) 直接执行 C++，覆盖相同 fixture 和额外反例；两类成绩分别报告。

本批补强了原 Python 参考实现的日期 AND/eq 检查、URI 检查和可信部署上限。端点限定为无 userinfo、无片段的绝对 ASCII HTTP(S) URI；Unicode 域名需先转为合法 ASCII 域名，路径和查询中的非 ASCII 或保留字符需正确编码。条款 URL 可带一个合法片段。含糊 IP 表示、非法端口、重复 DNS 尾点、裸路径方括号与错误百分号转义被拒绝。候选来源的非空审核日期及限额也必须内部一致，显式 reasoning 即使暂未使用也必须完整。

新增约束只影响配置校验，不改变原计划范围，也不把模型、联网抓取或来源准入标为已实现。
