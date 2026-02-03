# Bugbash 工作流脚本使用说明

更新说明见 [CHANGELOG.md](CHANGELOG.md)。

## 前置准备

### 1. 环境要求
- Python 3.9+
- Git 命令行工具

### 2. 安装依赖
```bash
pip install requests python-dotenv pathspec
```

### 3. 配置文件

复制 `.env.example` 为 `.env` 并修改配置：

```env
# GitHub 配置（必需）
DEFAULT_REPO_URL=git@github.com:your-username/your-repo.git
GITHUB_USERNAME=your_username
GITHUB_TOKEN=ghp_your_token_here

# 文件夹配置（必需）
MAIN_FOLDER_NAME=main
CUSTOM_FOLDERS=folder1,folder2,folder3

# 排除文件（可选，不配置则上传所有文件）
# EXCLUDE_NAMES=.git,__pycache__,.DS_Store

# PR 描述配置（可选，三选一）
# 方式1：从文件读取（配置后，每个文件夹必须有该文件）
PR_DESCRIPTION_FILE=final_prompt.txt
# 方式2：直接配置内容（所有 PR 使用相同描述）
# PR_DESCRIPTION=这是PR的描述
# 方式3：不配置，自动生成描述
```

如果你使用 `collect-artifacts`，并且同一台机器上同时安装了 Code 与 Code - Insiders，可在 `.env` 里追加：

```env
# collect-artifacts 日志级别（可选）：DEBUG / INFO / WARNING / ERROR
COLLECT_ARTIFACTS_LOG=INFO

# 默认 VS Code 变体（不指定时默认 Code）
VSCODE_VARIANT_DEFAULT=Code

# 显式声明哪些 model 文件夹是用 Code - Insiders 打开的（大小写不敏感，逗号分隔）
CODE_INSIDERS=oswe-mini-m23a3s430,grok-fast
```

**获取 GitHub Token：**
Settings → Developer settings → Personal access tokens → Generate new token (classic) → 勾选 `repo` 权限

---

## 命令使用

```bash
# 创建文件夹
python Bugbash_workflow.py create

# 同步 main 文件夹内容到其他文件夹
python Bugbash_workflow.py sync
python Bugbash_workflow.py sync --dry-run                    # 预览不执行
python Bugbash_workflow.py sync --targets folder1 folder2    # 指定目标

# 收集产物（生成各模型的 .txt 与 time.txt）
python Bugbash_workflow.py collect-artifacts

# 推送分支（不创建 PR）
python Bugbash_workflow.py push
python Bugbash_workflow.py push --force                      # 强制覆盖
python Bugbash_workflow.py push --collect-artifacts           # 推送前先收集产物（实验特性，默认关闭）

# 推送分支并创建 PR（推荐）
python Bugbash_workflow.py push-pr
python Bugbash_workflow.py push-pr --force                   # 强制覆盖所有分支
python Bugbash_workflow.py push-pr --folders folder1         # 只推送指定文件夹
python Bugbash_workflow.py push-pr --folders folder1 --force # 强制推送指定文件夹
python Bugbash_workflow.py push-pr --collect-artifacts        # 推送前先收集产物（实验特性，默认关闭）
```

说明：`--collect-artifacts` 只是帮你在推送前自动运行一次 `collect-artifacts`；也可以提前单独运行。

---

## 核心逻辑

### 智能推送机制

脚本采用智能分支创建和推送策略，自动适应不同场景：

**分支创建策略：**
1. **main 分支**：使用孤儿分支（独立历史）
2. **其他分支（远程已存在）**：基于 `origin/branch_name` 创建，保留提交历史
3. **其他分支（远程不存在）**：基于 `origin/main` 创建，继承 main 的提交历史

**推送策略：**
1. **内容完全一致**：Git commit 检测到无变化 → 跳过推送
2. **有变化且可 fast-forward**：直接推送成功
3. **有变化但无法 fast-forward**：提示使用 `--force` 强制覆盖

**为什么支持 fast-forward？**
- 远程分支已存在时，脚本基于远程分支创建本地分支
- 新增或修改文件后，Git 可以识别为正常的提交历史
- 不需要 `--force` 就能推送更新

**示例输出：**
```bash
python Bugbash_workflow.py push-pr

# == Pushing folder 'main' ==
#     ⊙ 内容与远程一致，跳过推送: main

# == Pushing folder 'folder1' ==
#     ✓ Pushed branch: folder1  # 新增文件，fast-forward 成功

# == Pushing folder 'folder2' ==
#     ⚠️ 远程分支 folder2 已存在且无法 fast-forward
#     ⊙ 如需覆盖，请使用: python Bugbash_workflow.py push-pr --force
```

### 文件夹结构
- **main 文件夹**：模板文件夹，存放基础代码
- **自定义文件夹**：在 `.env` 的 `CUSTOM_FOLDERS` 中配置，代表不同测试分支

### 推送规则

#### main 文件夹
- 推送到 `main` 分支（孤儿分支，无历史）
- Commit 信息：`input data`
- 不创建 PR
- 无需特殊文件

#### 自定义文件夹
- 基于远程 `main` 分支创建
- Commit 信息：文件夹名
- 可选创建 PR
- **必需文件**：`<文件夹名>.txt`（例如 `grok-fast` 文件夹需要 `grok-fast.txt`），且内容不能为空（仅空白也算空）
- **PR 描述**：根据 `.env` 配置自动处理 (e.g. final_prompt.txt)。若配置了 `PR_DESCRIPTION_FILE`，该文件也必须存在且内容不能为空

### PR 描述优先级
1. **从文件读取**：如果配置了 `PR_DESCRIPTION_FILE`，从每个文件夹的该文件读取（文件必须存在）
2. **从配置读取**：如果配置了 `PR_DESCRIPTION`，使用该内容（适合所有 PR 相同描述）
3. **自动生成**：如果都未配置，使用 `Auto-generated PR for branch: {分支名}`

### 文件过滤优先级
1. 优先使用 `.gitignore` 规则
2. 其次使用 `EXCLUDE_NAMES` 配置
3. 如果两者都为空，上传所有文件（仅排除 `.git` 文件夹）

---

## 注意事项

- ⚠️ `push / push-pr` 仍需要 `.env` 里的 GitHub 配置；但 `-h`/`create`/`sync`/`collect-artifacts` 可以在未配置 GitHub 项时运行
- ⚠️ 如果配置了 `PR_DESCRIPTION_FILE`，每个文件夹必须有该文件，否则会被跳过
- ⚠️ 自定义文件夹缺少 `<文件夹名>.txt` 或内容为空会被跳过
- ⚠️ **使用 `--folders` 补推时，必须确保远程 main 分支已存在**
- ⚠️ 无法 fast-forward 时需要使用 `--force` 覆盖（较少见）
- ✅ 首次推送建议：完整推送（包含 main），之后可单独补推
- ✅ 内容完全相同会自动跳过推送
- ✅ 新增或修改文件通常可以直接 fast-forward 推送，无需 `--force`
- ✅ PR 已存在不会报错，会自动跳过
- ✅ 单个文件夹失败不影响其他文件夹继续执行

### 推送场景说明

| 场景 | 行为 | 是否需要 --force |
|------|------|------------------|
| 远程不存在 | 直接推送 | ❌ 否 |
| 内容完全一致 | 跳过推送 | ❌ 否 |
| 新增/修改文件（可 fast-forward） | 直接推送 | ❌ 否 |
| 历史冲突（无法 fast-forward） | 提示需要强制覆盖 | ✅ 是 |

### 补推工作流示例

```bash
# 1. 首次完整推送（推送 main + 所有自定义文件夹）
python Bugbash_workflow.py push-pr

# 2. 如果某个文件夹失败，单独补推（前提：远程 main 已存在）
python Bugbash_workflow.py push-pr --folders folder1

# 3. 如果需要强制覆盖
python Bugbash_workflow.py push-pr --folders folder1 --force
```
