# 手动上传 GitHub（归档备选流程）

当前接手和 Git 推送记录见 [TAKEOVER_20261017.md](TAKEOVER_20261017.md)。下面是早期交接包的手工上传指南，不代表当前待执行任务。

目标仓库：`https://github.com/wmqfl861/bzs861`。本文件只指导你在自己的浏览器操作，不会登录、推送或覆盖仓库。

## 1. 选对文件

使用 `bzs861_github_upload_20261017.zip`，解压后进入 `bzs861/`。也可使用完整个人包里的 `01_repo_upload/bzs861/`，两处内容相同。

上传的是该目录**里面**的文件和文件夹，不是外层整个目录：

```text
仓库根目录/
  CMakeLists.txt
  CMakePresets.json
  README.md
  HANDOFF_START_HERE.md
  apps/
  include/
  src/
  tests/
  tools/
  docs/
  validation/
  .github/workflows/ci.yml
  .gitignore
  .clang-format
  .editorconfig
```

根目录不应变成 `bzs861_handoff_20261017/01_repo_upload/bzs861/...`，也不应只剩一个 ZIP。GitHub 不会因为上传 ZIP 就自动把其中源码展开成仓库文件。

不要上传个人完整包、`04_chats_private/`、`.local/`、`build/`、数据库、凭据、Cookie 或环境文件。完整聊天可能含个人和项目资料，应留在本地；需要分享时单独选择范围。

## 2. 已有仓库：保留原有内容

先打开仓库，看默认分支和已有文件。每次手工上传前重新核对仓库状态；本轮接手时的空仓库检查不能代表今后的状态。

选择新的开发分支，例如 `manual/bootstrap-m0-20261017`。若名字已存在，使用新的唯一分支名。然后选择 **Add file → Upload files**，拖入上述文件和文件夹；检查每个文件在仓库中的预期路径。

有同名文件时先比较，不要把“新分支”误当成自动解决覆盖问题。特别注意已有 README、LICENSE、工作流、配置和源码；保留对方修改或手工整合。

提交说明可用：

```text
feat: import bzs861 offline bootstrap and handoff documents
```

界面允许时选择新分支提交并创建 Pull Request；若已有保护规则阻止上传，遵守规则，不改保护设置来绕过。审查 diff 后再合并。

空仓库没有可选分支时，按页面给出的“上传已有文件”入口创建首次提交；已经有代码的仓库不要当成空仓库重新初始化。

## 3. 小心遗漏点开头的文件

文件管理器可能不显示 `.github`、`.gitignore`、`.clang-format` 和 `.editorconfig`。上传后逐项检查。必要时使用网页 **Add file → Create new file**，以准确路径创建缺少的文件，如 `.github/workflows/ci.yml`，内容从本包对应文件复制。

本包不包含 `.git/`，不需要把本地 Git 内部目录拖到网页。

## 4. 网页限制与上传验证

GitHub 官方文档说明：网页上传单个文件最多 25 MiB，一次最多 100 个文件，网页方式忽略 `.gitattributes` 的处理逻辑。[G1]

当前实际文件数量与最大文件见同目录 `UPLOAD_FILELIST.txt` 或外层 `06_checks/packaging_report.json`。文件大小在交付时已检查；规则或浏览器限制变化时，按 `apps/`、`src/`、`docs/` 等目录分批，始终保持相对路径，不随意改名。

上传完成后检查根目录 `CMakeLists.txt`、两个程序入口、测试、计划与工作流是否存在。查看本次提交的文件变更；网页上传不保留旧 bundle 的提交历史，也不能把已有本地测试记录当作 GitHub Actions 已运行。

可以在自己的环境按 README 构建和测试。Actions 没有运行或失败时保留状态，不直接在计划中标为通过。

## 5. 需要原 Git 历史时

个人完整包另有 bundle 和 patch；仅做网页上传无需使用它们。恢复原离线提交可执行：

```bash
git clone /path/to/bzs861_bootstrap_v0.0.1.bundle bzs861-offline
cd bzs861-offline
git log --oneline
```

该操作仅恢复本地历史。它不读取远程、不完成冲突合并，也不自动包含本次新增交接文档。实际远程整合应由了解 Git 的人基于真实仓库进行；不要强制推送。

## 依据

[G1] GitHub 官方：Adding a file to a repository。核对日期：2026-10-17。
`https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository`
