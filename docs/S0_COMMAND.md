# S0 探测命令（仅 D1 四来源）

一条可复制的命令，在你的机器上实测 D1 选定的四个来源：marginalia、mwmbl、wikipedia_en、wikipedia_zh。结果写入 `%USERPROFILE%` 下带时间戳的目录并打印路径。探测只是可用性观察：**`bzs probe` 退出 0 不等于来源获批，也不等于 S0/S1 验收通过**（背景见 [S0_PROBE.md](S0_PROBE.md)）。

## 前置条件

- 仓库检出已合入 CL-06 的 main（`mwmbl` 需要已注册的 `mwmbl_api` 适配器；若报 `source has no implemented adapter: mwmbl`，说明检出的代码不含 CL-06）。
- 已按 README 创建并激活 venv；在仓库根目录运行（命令里的 `config\sources.json` 是相对路径）。
- 不需要构建 searchd，也不需要 `bzs init`（命令显式指定 `--sources config\sources.json`）。

## 命令（PowerShell，整行复制）

```powershell
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'; $out = "$env:USERPROFILE\bzs861-s0-probe-$ts"; $env:BZS_MARGINALIA_KEY = 'public'; bzs probe --sources config\sources.json --include-candidates --only marginalia mwmbl wikipedia_en wikipedia_zh --per-source 3 --out $out; Write-Host "S0 probe results landed in: $out"
```

- `--per-source 3`：每来源至多 3 次查询预留（本轮合计 ≤12 次）；间隔 4 秒、超时 12 秒取默认值；遇 429/验证码立即停止该来源的剩余预算，不绕过访问控制。
- `--include-candidates --only <四个 ID>`：四个来源仍为 `enabled:false` 候选，仅本次显式探测，不改变核心/worker 准入，也不构成审核。
- 结束时命令打印结果目录；目录内为 `probe_report.md`、`probe_report.json` 与 `raw/` 原始响应。

## D15 共享 key 说明

- marginalia 的 key 经环境变量传入：`$env:BZS_MARGINALIA_KEY`。收到自己的 key 之前用共享 `public`——**共享 public key 仅限 S0 探测**。
- 适配器在该变量为空时会静默退回 `public`；本命令显式设为 `public` 以免歧义。收到自己的 key 后把 `'public'` 替换成它再跑一次；S1 签收（`tools/accept_s1.py`）必须使用自己的 key，检测到启用状态下使用共享 public key（含适配器回退）会直接判 FAIL。
- mwmbl 与两个 wikipedia 来源匿名访问，不需要 key。

## 结果如何交回

- 把打印出的结果目录路径交回审核（至少包含 `probe_report.md`/`probe_report.json`）：`reachable`/`blocked`/`needs_work` 只是本次有限样本的可达性结论，正式启用需另行完成来源审核。
- 退出码：存在 ok/empty 响应为 0；全部失败 1；输入错误 2；中断 130。任何退出码都不代表来源通过。
- `raw/` 保存原始响应（外部不可信数据，可能含地区信息），分享前先本地检查。
