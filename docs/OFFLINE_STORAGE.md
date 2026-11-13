# 旧库离线存储维护

这是当前原库的显式本机维护，不是备份导入或恢复接口。只接受业务 schema15、当前 gram/2 派生索引格式且 `restore_quarantine=0` 的普通单链接数据库文件。拒绝符号链接、未知格式、自定义视图/触发器、未知虚拟表和隔离备份。不会清除隔离、补写撤销连续性、改变带键身份保留期或启用来源。

先停止 searchd 和所有访问此库的进程。示例路径须替换成配置中的 `state_dir/metadata.sqlite`：

```sh
bzs stop
bzs storage inspect /absolute/state/metadata.sqlite
bzs storage convert /absolute/state/metadata.sqlite --confirm-offline --timeout-ms 120000
```

`bzs storage` 只调用配置中 searchd 同目录的 searchctl，绝不自动启动服务。也可直接执行 `searchctl storage-inspect PATH` 或 `searchctl storage-convert PATH --confirm-offline [--timeout-ms N]`。转换不是在线业务，不经 MCP/HTTP 暴露。

## 执行与失败边界

inspect 使用只读连接，不构造 Store、不迁移、不清理业务记录。convert 首先在同一 SQLite 句柄上取得持久排它锁；锁被占用即返回 `E_STORAGE_BUSY`，不会猜测 PID 或删除锁/WAL 文件。调用者仍须停服；这个工具不对抗拥有数据库目录写权限、在检查后替换文件的同一用户。

转换需要原库目录及临时目录各至少 `3 × 数据库逻辑字节 + 16 MiB` 可用空间。这是保守准入检查，不是预留空间或完整磁盘故障保证。原库保持排它锁时，通过 SQLite backup API 生成自动删除的临时数据库，先在临时库设置 INCREMENTAL 并 VACUUM。对表结构、每张普通/虚拟/影子表的类型化行内容、rowid、FTS 完整性和外键进行比较。内部临时摘要不入业务数据库，不导出正文，不建立新的业务证明链。

VACUUM 可能重排没有整数主键的 rowid，因此预演只要发现任何 rowid/索引对应变化就拒绝，原库不做转换；工具不会“修复”成另一套编号。只有预演一致，才在同一仍持锁的原库上执行 SQLite 日志保护的 VACUUM 并再次核验。没有用旧副本替换当前文件，也没有通过备份回滚撤销记录。

超时范围为 1..600000 毫秒，使用单调时钟及 SQLite 进度回调；这是可中断 SQL 的期限，不是物理磁盘 I/O 的硬实时上限。预演或源 VACUUM 中断会报错，不自动重试；成功改写后若复核未完成，只会收紧既有隔离标志并返回 `E_MAINTENANCE_QUARANTINED`。若隔离写入本身也失败，则返回 `E_MAINTENANCE_UNVERIFIED`：必须保持停服并人工检查，不能假定自动隔离成功。不要手动将隔离值改回 0。

成功结果包含前后页数、模式及普通表检查数量，不包含业务正文。已是 INCREMENTAL 则先执行 SQLite 与 FTS 完整性检查，再报告 `already_incremental`，不重复全库重写；发现不一致仍返回失败，不把“无需转换”当作“数据库健康”。之后普通压缩仍沿用每次最多128页的增量回收限制。本轮不代表断电、坏盘、可信备份恢复或完整 P2-A 验收。

实现依据：SQLite 官方 `lang_vacuum.html`、`pragma.html#pragma_locking_mode`、`c3ref/backup_finish.html`。测试使用可丢弃的离线合成数据库，不访问用户实际数据库。
