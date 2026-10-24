# 冻结整合输入的独立合并核对

**结论：通过；未发现合并丢失、额外生产修改或冲突残留。** 核对对象为根工作树 `/workspace/scratch/e2371129386d/bzs861` 的冻结产品输入，检查时 HEAD 为 `e066753af9bd8888eefa5d694e83f677d09a48a5`，P2-9 已合入工作树与 index、尚未形成该项根提交。结论绑定逐文件字节与 index blob，不预写未来提交。

这是整合者 root 与作者版本之间的只读关系核对。审核者 search_lock 是 P2-5 作者，因此这里**不重新审签自己的 P2-5 实现**；P2-5 实现的独立审核仍以 dispatcher 的既有报告为准。未执行生产测试，也未修改根工作树、Git index 或运行中的日志。

## 输入和比较方法

共同基线为 `59081e14fdda765844031e13f874e588334f1a36`。作者输入分别为：

| 范围 | 已固定作者/实现提交 |
| --- | --- |
| P2-5 | `1c69836f393aee55032beae4bd027f4d286d1e4b` |
| P2-6 | `a9bac84266ee603e27e3957b80a54387e2c44820` |
| P2-7 | `23cb1ceffbbc0e65dbcbc83baf44ac4a2159fedc` |
| P2-9（含首版与回拨修正） | `a30231a802ab05f0bb3f821c51ef03d156d796d8` |

范围内变更共 **22 个文件**，恰好等于四份作者改动的并集。18 个仅被一个作者改变的文件，根工作树与对应作者 Git blob 逐字节相同。所有 22 个文件也与根当前 index 相同，检查前后字节未变化。详细文件列表、完整 SHA256、index blob、来源提交和关系见 [merge-checks.json](merge-checks.json)。检查程序为 [check_merge.py](check_merge.py)。

## 四处交叠

1. **`src/store.cpp` 与 `tests/unit.cpp`**：从 Git 取出基线、P2-5 和最终 P2-9 的原字节，仅在临时目录调用 `git merge-file -p --diff3`。左右方向分别重建，两种方向都退出 0、没有冲突，输出均逐字节等于根当前文件。因此根文件保留两份原改动，没有人工改写混入。P2-5 前置根 HEAD 中的这两个文件也逐字节等于 P2-5 作者提交。
2. **`include/bzs/web.hpp`**：独立确认 P2-6 对基线仅替换 WorkerPort 并发调用合同注释，P2-9 对基线仅加入搜索退避注释与 `source_retry_after_limit_ms=24LL*60*60*1000` 常量。把这两项精确变更合并后，整份头文件逐字节等于根当前文件，没有其他声明或代码变化。
3. **`apps/searchd/main.cpp`**：P2-6 作者相对基线仅将一个 `std::jthread worker_thread` 声明改为 `BackgroundWorker worker_thread`。对已审 P2-7 的 main.cpp 做相同唯一替换，结果逐字节等于根当前文件，也等于根已提交的 P2-6 整合版本 `8f753c24`。请求池、IO、准入、503、停止和对象生命周期代码没有在本次接线中再改写。

关键整合文件 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `src/store.cpp` | `1b7acb994470a01987ddef5ccb653c8c988a5355ddd2e2ca9fc7c8145bc1d7e2` |
| `tests/unit.cpp` | `9eee0b9fa8d55b8029beb19267f03ad5afb11e9511678f5e33ceb9201741eaf2` |
| `include/bzs/web.hpp` | `f57ed6661ee0ac37a7c5371ff1d16df10171e4fabf1ddf6b40d078cafa9be671` |
| `apps/searchd/main.cpp` | `d362138a54e6f8287c0702ac66b1755fbc0976dea38f487ce17c40227d4a2e33` |

## P2-9 和正式回归保持

P2-9 的 `src/web.cpp`、四个 Python 生产模块、两个 Python 测试文件及 `docs/SOURCE_BACKOFF.md` 均逐字节等于 `a30231a`。首版 743 已被修正的时钟回拨逻辑没有在整合时回退。Store 的证明、旧极端值处理和正式 unit 回归均包含在上述整份文件合并等同性中。

另核对 unit 注册名：基线 88 个，P2-5 作者 97 个，P2-9 作者 96 个，根当前 **105 个**；是两作者集合的完整并集，没有重名或遗漏。检查的是源码注册与整份测试文件的保留关系，**不是 105 项运行通过的声明**。

来源/站点配置及 CMakeLists/CMakePresets 逐字节保持基线；本次关系核对没有修改来源启用、版本或测试设置。正在进行的完整 CTest、pytest、sanitizer 结果应由 root 的实际执行报告独立记录，后续最终交接证据再单独审核。
