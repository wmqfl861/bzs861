# 配置合同 v1.1
本目录包含查询、来源、模型3份JSON Schema及4份配置正例，其中新增model-config.no-model.example.json。
所有示例均为设计样例，不授权任何实际外网请求。没有网络调用、没有真实密钥。
验证流程：先检查请求大小/AST复杂度，再进行启用FormatChecker的结构校验，接着规范化/语义校验，最后由未来产品检查真实授权/出站/模型能力/部署硬上限。
default不是自动补值。null、字段缺省、显式覆盖分别有语义。普通文本角色可继承；专门能力不能盲目继承。
v1.0迁移：模型配置可不提供任何模型；角色可省略profile而继承；新增max_concurrency/retry_limit覆盖。启用来源需补逻辑服务标识、可信审核者及expires_at，不能自动编造批准/到期时间。非法field/operator组合现在会更早拒绝。
Python参考执行：python tools/validate_contracts.py
运行需要jsonschema。该脚本不是产品授权器或模型能力探测器，不能作为网络安全保证。
