# S2 五阶段验证记录

当前为0.1.6第一检查点：[报告](stage1-report.json)、[v2冻结执行输入](stage1-v2-execution-inputs.json)、[v2独立复核摘要](stage1-v2-review.json)。后续阶段使用新命名记录，已执行命令与日志保持不变。

修复后的最终门槛为12套CTest、6套ASan/UBSan、34项原设计合同和13项规划守卫，全部通过，0跳过。独立复核17个公开API竞态、2个真实schema7在途升级和6个旧Worker不完整回包，共25例通过。0.1.5原始7个缺陷复现保留。

最初候选曾通过其时已知门槛，后续独立复核发现旧Worker截断/206/500回包的完整性缺口。该候选在stage1-initial-report.json明确标记被取代，原输入、命令、日志和实际失败仍保留；当前报告只引用修复后重新冻结的stage1-v2记录。stage1-source-manifest.json和stage1-review.json属于最初候选，不能当作当前代码的最终身份。

最初stage1-asan-*.log中不带final的6个记录是沙箱ptrace导致LeakSanitizer不能工作的环境失败；未关闭检测、未更改当时二进制，同输入重跑通过。最终v2同样开启detect_leaks=1并通过。

执行前冻结代码、构建、测试和配置输入，每个命令记录SHA及前后身份。报告由包含它的Git提交绑定，发布后另核对该精确提交Linux/Windows CI。回环测试不代表真实来源S0、人工质量或完整83工作包/161验收签收。旧schema头文件可从2007690c提取；日志记录其SHA，临时数据库和二进制不公开。
