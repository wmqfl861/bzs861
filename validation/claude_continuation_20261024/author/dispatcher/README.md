# P2-6 作者验证

基线为 `59081e14fdda765844031e13f874e588334f1a36`。固定 fixture、read、map/crawl、持久搜索四条执行通道，每条最多一个步骤；没有额外协调线程或内存预领取队列。线程全部创建完成前使用共享启动闸门；任何部分启动失败先全停、开闸，再 join。默认无外部 Worker 时只启动 fixture 通道。`BackgroundWorker` 的主程序类型适配只有一行，包含在本项提交中。

最终作者结果：Debug fairness **18 例 / 339 断言**；ASan/UBSan 相同 **18 / 339**（泄漏检测保持开启）；完整真实 crawl E2E **26 例**，均通过。新增七条 native 回归和两条真实 Worker 闸门回归。正式用例、真实源码/二进制哈希、命令与退出码见 `author-result.json` 及对应 command JSON/log。

相同最终正式原生测试链接上一轮真实库时，read、discovery、search 三个阻塞方向均在跨类别进展断言失败。新真实 HTTP 回归在上一轮真实 searchd 上均失败；对应五条旧失败保留。闸门只有在其他类别完成或断言失败清理时才放行，所以这证明执行顺序，不将 5 秒测试挂起上限称为性能指标。

第一次最终 ASan 在默认跟踪 sandbox 下被 LeakSanitizer 以 ptrace 不兼容中止，`fairness-asan-final.*` 保留且不计通过；相同输入及二进制在非跟踪宿主执行的 `fairness-asan-final-host.*` 全过，没有关闭泄漏检测、改 watchdog 或减少用例。历史阶段及预检日志均保留。`run.py` 的早期记录只枚举 tracked 输入；最终记录同时枚举 tracked 和 untracked 的非 validation 文件，并绑定 runner 自身哈希。

原 fairness 的全局 read/map 严格交替由独立通道替代；改成验证类别内 read 位置顺序、robots 先于页面以及各类别进展。新的并发取消回归仍验证迟到读取和 crawl 正文不发布，已知用量正确结算，未知 RPC 失败保留预留且不重发。

作者验证不是整合后的完整验收。根代理还需运行整合代码的全部 CTest、pytest、15 个原生 ASan/UBSan 程序，并核对最终发布 SHA 的 Linux/Windows CI。只验证本项范围，来源启用、六项保留决定及工作包签收均不变。单类别内部仍串行，map/crawl 共用一个通道，正在执行的 RPC 按原有限超时收尾；启动资源失败路径已静态复核但未注入线程创建失败。
