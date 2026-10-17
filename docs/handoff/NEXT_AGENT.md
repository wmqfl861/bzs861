# 下一批开发任务书

继续开发 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)（不周山搜索工具）。本项目是独立 C++20 搜索、网页读取、爬虫、内容索引与证据系统；Linux 优先，Windows 为目标。先读取真实 main、提交历史和工作区状态，不以本次空仓库基线代替未来现状。

## 先读

1. `HANDOFF_START_HERE.md`、`docs/handoff/TAKEOVER_20261017.md`、`README.md`。
2. `docs/DEVELOPMENT_STATUS.md`、`docs/implementation_status.json`、`validation/takeover/test_report.json`。
3. `docs/API.md`、`docs/CONFIGURATION.md`、`docs/SECURITY.md`。
4. `docs/planning/development_blueprint_v1.1.md`、`functional_spec_v0.3.md`、`requirements_and_backlog.json`。

## 当前实际实现

0.0.2 离线切片：fixture/显式本地导入、HTML/TXT正文、SQLite不可变快照、FTS5 ASCII候选、字面检索、冻结分页、原文证据、用户隔离、fixture任务与隔离备份。新增原生 `contract.validate`，可检查三类正式配置，但返回 `applied=false`，不注册配置或发出外部请求。

当前测试为核心52组/289断言、原生合同55组/261断言、HTTP/CLI20组，另保留Python34组设计基线；sanitizer与CI按实际报告核对。独立复核已经执行，范围仅接手切片，不代表完整83工作包/161验收项完成。仍是21部分实现、62未开始、0完整工作包验收。

数据库schema 2只重建FTS副本，不改写快照和证据。HTML解析版本bootstrap-html/2只用于新导入；旧HTML不会自动清洗。隔离备份仍不能安全恢复后启动服务。

## 下一批顺序

1. 核验当前commit的Linux/Windows CI和源文件哈希，保留实际失败，不把工作流存在当成已通过。
2. 继续CONTRACT-02：受信部署配置加载、配置注册/引用一致性、source/collection/model真实身份、变更版本绑定。当前严格整数表示与完整JSON-Schema数学整数不同；若加入1.0/1e0，需保留原词法并做精确十进制归一化，不能依靠double舍入。
3. 独立撤销日志与隔离恢复：旧备份不能复活删除内容和撤销权限；日志连续性不明时保持隔离。
4. 独立解析进程和网页出站边界：URL、DNS、重定向、子请求与模型内网权限分别处理。
5. 完成HTTP获取、预算和robots准入后，再接第一个真实审核通过的免费通用网页源。来源合同校验不证明审核真实性。
6. 最小MCP/UI复用既有业务与鉴权，只暴露实际已实现操作。

## 保留的要求

自己实现业务能力，不部署或调用SearXNG、Scrapling、Firecrawl、Exa成品后端。非模型外部服务只能使用经过审核的免费入口，排除试用额度、付费自动回退。用户明确配置的本地/远程模型可在获得执行授权后接入；不自动豁免其托管搜索/浏览器服务。

完整83项范围不删减，多媒体、订阅和私人资料等继续按依赖分期。不要混入pi861/dsh930的通用Agent编排或视频测试需求。模型端点和密钥通过受信配置引用提供，不硬编码真实凭据。工作区和原始交接聊天不公开发布。

每次改动绑定功能/工作包及有意义的回归，区分代码、Schema设计、独立审核和全产品验收。先读真实远端，不强推、不覆盖未知改动；通过已授权连接提交后核验远端SHA，并保证工作副本一致。
