# Bugbash 工作流脚本使用说明

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

# 推送分支（不创建 PR）
python Bugbash_workflow.py push
python Bugbash_workflow.py push --force                      # 强制覆盖

# 推送分支并创建 PR（推荐）
python Bugbash_workflow.py push-pr
python Bugbash_workflow.py push-pr --force                   # 强制覆盖
python Bugbash_workflow.py push-pr --folders folder1 folder2 # 指定文件夹
```

---

## 核心逻辑

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
- **必需文件**：`<文件夹名>.txt`（例如 `grok-fast` 文件夹需要 `grok-fast.txt`）
- **PR 描述**：根据 `.env` 配置自动处理 (e.g. final_prompt.txt)

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

- ⚠️ 必需的 `.env` 配置项未设置会导致脚本退出
- ⚠️ 如果配置了 `PR_DESCRIPTION_FILE`，每个文件夹必须有该文件，否则会被跳过
- ⚠️ 自定义文件夹缺少 `<文件夹名>.txt` 会被跳过
- ⚠️ `--force` 会覆盖远程分支，谨慎使用
- ✅ 建议首次推送顺序：先推 main，再推其他分支
- ✅ PR 已存在不会报错，会自动跳过
- ✅ 单个文件夹失败不影响其他文件夹继续执行
