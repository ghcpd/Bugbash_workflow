# Changelog

## 2026-02-03

### collect-artifacts 配置简化（VS Code 变体选择）
- 修复了查找路径时的逻辑判断问题
- 新增 `CODE_INSIDERS=model1,model2`：显式声明哪些 model 文件夹是用 Code - Insiders 打开的
- 其余 model 文件夹使用 `VSCODE_VARIANT_DEFAULT`（默认 `Code`）

## 2026-01-30

### 产物收集集成（实验特性）+ 推送前校验增强
- 新增 `collect-artifacts` 命令：生成各模型的 `<model>.txt` 与 `time.txt`
- `push / push-pr` 默认不再自动收集产物；可用 `--collect-artifacts` 显式开启（实验特性）
- 推送前校验增强：非 main 文件夹必须存在且**非空**的 `<文件夹名>.txt`；若配置了 `PR_DESCRIPTION_FILE`，也必须存在且**非空**
- `.env` 不再在启动阶段强制要求；仅在需要推送/创建 PR 时才会校验必需项

## 2026-01-09

### 智能推送和分支管理优化
- **智能分支创建**：自动检测远程分支，已存在则基于远程分支创建，支持 fast-forward 推送
- **内容比较优化**：commit 时自动检测是否有变化，无变化则跳过推送
- **简洁输出**：内容一致时直接显示跳过信息，不显示冗余的 Git 错误输出
- **灵活推送**：先尝试普通推送，失败后再根据 `--force` 参数决定是否强制覆盖
- **补推优化**：使用 `--folders` 指定文件夹时，只推送指定的文件夹
- **远程检查**：使用 `--folders` 推送非 main 分支时，自动检查远程 main 是否存在
