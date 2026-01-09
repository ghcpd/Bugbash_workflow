#!/usr/bin/env python3
"""
Bugbash工作流脚本：创建文件夹、同步内容、推送分支并创建PR
使用方法：
  python Bugbash_workflow.py create           # 创建文件夹
  python Bugbash_workflow.py sync             # 同步main文件夹内容到其他文件夹，强制覆盖，不会清空文件夹。
  python Bugbash_workflow.py push             # 推送文件夹为分支
  python Bugbash_workflow.py push-pr          # 推送文件夹为分支并创建PR（推荐，首次推送）
  python Bugbash_workflow.py push-pr --force  # 强制推送并创建PR（更新已存在的分支）
"""
import argparse
import os
import shutil
import subprocess
import tempfile
import requests
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import time
import pathspec

# 加载 .env 文件
load_dotenv()

# ========================================
# 【配置区域】- 从 .env 文件读取
# ========================================
def get_required_env(key: str, error_msg: str = None) -> str:
    """获取必需的环境变量，如果未配置则退出程序"""
    value = os.getenv(key)
    if not value:
        msg = error_msg or f"❌ 错误：未在 .env 文件中配置 {key}"
        print(msg)
        print(f"请在 .env 文件中设置 {key}，参考 .env.example 文件")
        raise SystemExit(1)
    return value

# GitHub 仓库配置
DEFAULT_REPO_URL = get_required_env('DEFAULT_REPO_URL', "❌ 错误：未在 .env 文件中配置 DEFAULT_REPO_URL（GitHub仓库URL）")
GITHUB_USERNAME = get_required_env('GITHUB_USERNAME', "❌ 错误：未在 .env 文件中配置 GITHUB_USERNAME（GitHub用户名）")
GITHUB_TOKEN = get_required_env('GITHUB_TOKEN', "❌ 错误：未在 .env 文件中配置 GITHUB_TOKEN（GitHub访问令牌）")

# 模板文件夹名称
MAIN_FOLDER_NAME = get_required_env('MAIN_FOLDER_NAME', "❌ 错误：未在 .env 文件中配置 MAIN_FOLDER_NAME（模板文件夹名称）")

# 要创建的自定义文件夹名称列表（也会作为分支名和PR标题）
_custom_folders_str = get_required_env('CUSTOM_FOLDERS', "❌ 错误：未在 .env 文件中配置 CUSTOM_FOLDERS（自定义文件夹列表）")
CUSTOM_FOLDERS = [f.strip() for f in _custom_folders_str.split(',') if f.strip()]

# 排除的文件/文件夹名称（可选，如果未配置则上传所有文件）
_exclude_names_str = os.getenv('EXCLUDE_NAMES', '')
EXCLUDE_NAMES = {n.strip() for n in _exclude_names_str.split(',') if n.strip()} if _exclude_names_str else set()

# PR 描述配置（两个二选一：从文件读取或直接配置内容）
PR_DESCRIPTION_FILE = os.getenv('PR_DESCRIPTION_FILE')  # PR描述文件名（可选）
PR_DESCRIPTION = os.getenv('PR_DESCRIPTION')  # PR描述内容（可选）
# ========================================


# ========================================
# Create 命令相关函数
# ========================================
def safe_mkdir(path: Path) -> None:
    """安全地创建目录，如果目录已存在则不报错"""
    path.mkdir(parents=True, exist_ok=True)


def cmd_create(args):
    """创建文件夹命令"""
    root = Path.cwd()

    # 创建模板文件夹（main文件夹）
    main_dir = root / MAIN_FOLDER_NAME
    safe_mkdir(main_dir)

    # 创建用户指定的自定义文件夹
    created = []
    for name in CUSTOM_FOLDERS:
        if name.strip() == "":  # 跳过空名称
            continue
        if name == MAIN_FOLDER_NAME:  # 避免与模板文件夹名冲突
            continue
        d = root / name
        safe_mkdir(d)
        created.append(name)

    # 打印创建结果
    print(f"Workspace: {root}")
    print(f"Template folder ensured: {MAIN_FOLDER_NAME}/")
    print(f"Custom folders ensured ({len(created)}): {', '.join(created)}")


# ========================================
# Sync 命令相关函数
# ========================================
def copy_tree(src: Path, dst: Path, dry_run: bool) -> None:
    """递归复制目录树"""
    # Ensure dst exists
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(src):
        root_path = Path(root)

        # Prune excluded dirs in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES and d != ".git" and not d.startswith(".")]

        rel = root_path.relative_to(src)
        dst_root = dst / rel

        if not dry_run:
            dst_root.mkdir(parents=True, exist_ok=True)

        for f in files:
            if f in EXCLUDE_NAMES:
                continue
            src_file = root_path / f
            dst_file = dst_root / f

            if dry_run:
                print(f"[DRY] copy {src_file} -> {dst_file}")
            else:
                shutil.copy2(src_file, dst_file)


def autodetect_targets(root: Path, main_name: str) -> list[Path]:
    """自动检测目标文件夹（仅检测 CUSTOM_FOLDERS 中定义的文件夹）"""
    targets = []
    for folder_name in CUSTOM_FOLDERS:
        if folder_name.strip() == "":
            continue
        if folder_name == main_name:
            continue
        folder_path = root / folder_name
        if folder_path.is_dir():
            targets.append(folder_path)
    return targets


def cmd_sync(args):
    """同步文件夹命令"""
    root = Path.cwd()
    main_dir = root / args.main_name
    if not main_dir.is_dir():
        raise SystemExit(f"Template folder not found: {main_dir}")

    if args.targets:
        targets = [root / t for t in args.targets]
    else:
        targets = autodetect_targets(root, args.main_name)

    targets = [t for t in targets if t.is_dir()]
    if not targets:
        print("No target folders found.")
        return

    print(f"Template: {main_dir}")
    print(f"Targets ({len(targets)}): {', '.join([t.name for t in targets])}")
    for t in targets:
        copy_tree(main_dir, t, args.dry_run)

    print("Sync done.")


# ========================================
# Push 命令相关函数
# ========================================
def run(cmd: list[str], cwd: Path) -> None:
    """运行命令"""
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        # 显示错误信息
        stderr_output = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
        stdout_output = e.stdout.decode('utf-8', errors='replace') if e.stdout else ''
        print(f"\n❌ 命令执行失败: {' '.join(cmd)}")
        if stdout_output:
            print(f"标准输出:\n{stdout_output}")
        if stderr_output:
            print(f"错误输出:\n{stderr_output}")
        raise


def copy_folder_to_repo_root(src_folder: Path, repo_dir: Path) -> None:
    """复制文件夹内容到仓库根目录（上传所有文件）
    
    Args:
        src_folder: 源文件夹路径
        repo_dir: 目标仓库目录
    """
    # Remove everything except .git
    for item in repo_dir.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # 检查是否存在 .gitignore 文件
    gitignore_file = src_folder / ".gitignore"
    gitignore_spec = None
    
    # 检查 EXCLUDE_NAMES 是否为空
    use_exclude_names = bool(EXCLUDE_NAMES)
    
    if gitignore_file.exists() and gitignore_file.is_file():
        try:
            with open(gitignore_file, 'r', encoding='utf-8') as f:
                gitignore_spec = pathspec.PathSpec.from_lines('gitwildmatch', f)
            print(f"    ⓘ 使用 .gitignore 规则过滤文件")
        except Exception as e:
            print(f"    ⚠️ 读取 .gitignore 失败: {e}")
            gitignore_spec = None
    
    if not use_exclude_names and not gitignore_spec:
        print(f"    ⓘ 未配置 EXCLUDE_NAMES 且无 .gitignore，将上传所有文件（仅排除 .git 文件夹）")
    
    # Copy src folder contents into repo root
    for root, dirs, files in os.walk(src_folder):
        root_path = Path(root)
        rel = root_path.relative_to(src_folder)
        
        # 如果有 .gitignore，使用它的规则；否则使用默认规则或上传所有
        if gitignore_spec:
            # 使用 .gitignore 规则过滤目录
            dirs[:] = [d for d in dirs if not gitignore_spec.match_file(str(rel / d)) and d != ".git"]
        elif use_exclude_names:
            # 使用默认规则
            dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES and d != ".git"]
        else:
            # 上传所有目录（仅排除 .git）
            dirs[:] = [d for d in dirs if d != ".git"]
        
        dst_root = repo_dir / rel
        dst_root.mkdir(parents=True, exist_ok=True)

        for f in files:
            file_rel_path = rel / f
            
            # 如果有 .gitignore，使用它的规则；否则使用默认规则或上传所有
            if gitignore_spec:
                # 使用 .gitignore 规则判断
                if gitignore_spec.match_file(str(file_rel_path)):
                    continue
            elif use_exclude_names:
                # 使用默认规则
                if f in EXCLUDE_NAMES:
                    continue
            
            # 上传文件
            try:
                shutil.copy2(root_path / f, dst_root / f)
            except Exception as e:
                print(f"    ⚠️ 复制文件失败，跳过: {file_rel_path}")
                print(f"       错误: {e}")
                continue


def get_pr_description(folder: Path) -> str:
    """从文件夹中读取PR描述或使用环境变量配置的内容"""
    # 如果配置了 PR_DESCRIPTION_FILE，优先从文件读取
    if PR_DESCRIPTION_FILE:
        desc_file = folder / PR_DESCRIPTION_FILE
        if desc_file.exists() and desc_file.is_file():
            try:
                with open(desc_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    return content
            except Exception as e:
                print(f"    ⚠️ 读取 {PR_DESCRIPTION_FILE} 失败: {e}")
    # 如果没有配置 PR_DESCRIPTION_FILE，使用 PR_DESCRIPTION 环境变量
    elif PR_DESCRIPTION:
        return PR_DESCRIPTION
    
    # 默认描述
    return f"Auto-generated PR for branch: {folder.name}"


def get_default_branch(repo_url: str, username: str, token: str) -> str:
    """获取仓库的默认分支"""
    # 从 repo_url 提取仓库名
    # 支持 SSH 和 HTTPS 格式
    if "github.com:" in repo_url:
        # SSH: git@github.com:username/repo.git
        repo_name = repo_url.split(":")[-1].replace(".git", "")
    else:
        # HTTPS: https://github.com/username/repo.git
        repo_name = repo_url.split("github.com/")[-1].replace(".git", "")
    
    # 只取仓库名（去掉用户名部分）
    if "/" in repo_name:
        repo_name = repo_name.split("/")[-1]
    
    api_url = f"https://api.github.com/repos/{username}/{repo_name}"
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'Authorization': f'token {token}'
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code == 200:
            return response.json()['default_branch']
    except Exception as e:
        print(f"    ⚠️ 获取默认分支失败: {e}")
    
    return 'main'  # 默认返回 main


def create_pull_request(repo_url: str, branch_name: str, pr_title: str, 
                       pr_body: str, username: str, token: str) -> bool:
    """创建 Pull Request"""
    # 从 repo_url 提取仓库名
    if "github.com:" in repo_url:
        # SSH: git@github.com:username/repo.git
        repo_name = repo_url.split(":")[-1].replace(".git", "")
    else:
        # HTTPS: https://github.com/username/repo.git
        repo_name = repo_url.split("github.com/")[-1].replace(".git", "")
    
    # 只取仓库名（去掉用户名部分）
    if "/" in repo_name:
        repo_name = repo_name.split("/")[-1]
    
    # 获取默认分支
    base_branch = get_default_branch(repo_url, username, token)
    
    api_url = f"https://api.github.com/repos/{username}/{repo_name}/pulls"
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'Authorization': f'token {token}'
    }
    data = {
        'title': pr_title,
        'head': branch_name,
        'base': base_branch,
        'body': pr_body
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=data)
        if response.status_code == 201:
            pr_url = response.json()['html_url']
            pr_number = response.json()['number']
            print(f"    ✓ 已创建 PR #{pr_number}: {pr_url}")
            return True
        elif response.status_code == 422:
            error_msg = response.json().get('errors', [{}])
            if error_msg and 'message' in error_msg[0]:
                msg = error_msg[0]['message']
                if 'pull request already exists' in msg.lower() or 'A pull request already exists' in msg:
                    print(f"    ⊙ PR 已存在")
                    return True
            print(f"    ⚠️ 创建 PR 失败: {response.status_code}, {response.text}")
            return False
        else:
            print(f"    ⚠️ 创建 PR 失败: {response.status_code}, {response.text}")
            return False
    except Exception as e:
        print(f"    ⚠️ 创建 PR 异常: {e}")
        return False


def cmd_push(args):
    """推送文件夹为分支命令"""
    root = Path.cwd()

    if args.folders:
        # 用户明确指定了文件夹，只推送指定的文件夹
        folders = [root / f for f in args.folders]
    else:
        # 自动检测所有文件夹（不包括 main）
        folders = autodetect_targets(root, args.main_name)
        
        # 只有在自动检测模式下，才检查并添加 main 文件夹
        main_folder = root / args.main_name
        if main_folder.is_dir():
            # 将 main 文件夹添加到列表开头，优先推送
            folders.insert(0, main_folder)

    folders = [f for f in folders if f.is_dir()]
    
    if not folders:
        raise SystemExit("No folders to push.")

    print(f"\n{'='*50}")
    print(f"Repo: {args.repo_url}")
    print(f"Folders ({len(folders)}): {', '.join([f.name for f in folders])}")
    print(f"Create PR: {'Yes' if args.create_pr else 'No'}")
    print(f"{'='*50}\n")

    with tempfile.TemporaryDirectory(prefix="push_branches_") as tmp:
        tmp_dir = Path(tmp)

        # init empty repo
        run(["git", "init"], tmp_dir)
        run(["git", "remote", "add", "origin", args.repo_url], tmp_dir)
        # Fetch remote refs (so push can set upstream cleanly)
        try:
            run(["git", "fetch", "origin", "--prune"], tmp_dir)
        except subprocess.CalledProcessError:
            # If repo is empty or auth issues, git may fail; still allow pushing new branches if auth ok.
            pass
        
        # 检查是否有非 main 文件夹需要推送
        has_non_main = any(f.name != args.main_name for f in folders)
        if has_non_main:
            # 检查远程 main 分支是否存在
            try:
                result = subprocess.run(
                    ["git", "ls-remote", "--heads", "origin", args.main_name],
                    cwd=str(tmp_dir),
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if not result.stdout.strip():
                    # 远程 main 分支不存在
                    print(f"❌ 错误：远程 {args.main_name} 分支不存在")
                    print(f"⊙ 请先推送 {args.main_name} 文件夹作为基础分支：")
                    print(f"   python Bugbash_workflow.py push-pr")
                    raise SystemExit(1)
            except subprocess.CalledProcessError as e:
                print(f"⚠️ 无法检查远程分支状态: {e}")
                print(f"⊙ 继续执行，但如果远程 {args.main_name} 不存在可能会失败")

        for folder in folders:
            branch = folder.name
            
            # 判断是否是 main 文件夹
            is_main = (folder.name == args.main_name)
            
            if is_main:
                print(f"== Pushing folder '{branch}' as default branch '{branch}' ==")
                # main 文件夹不需要 final_prompt.txt
            else:
                print(f"== Pushing folder '{branch}' as branch '{branch}' ==")
                
                # 检查是否存在与文件夹同名的txt文件
                folder_txt_file = folder / f"{folder.name}.txt"
                if not folder_txt_file.exists() or not folder_txt_file.is_file():
                    print(f"    ⚠️ 未找到 {folder.name}.txt，跳过该文件夹")
                    print(f"    ⊙ 请在 {folder} 中创建 {folder.name}.txt 文件")
                    print()
                    continue
                
                # 检查 PR 描述配置：如果配置了 PR_DESCRIPTION_FILE，必须找到该文件
                if PR_DESCRIPTION_FILE:
                    desc_file = folder / PR_DESCRIPTION_FILE
                    if not desc_file.exists() or not desc_file.is_file():
                        print(f"    ⚠️ 未找到 {PR_DESCRIPTION_FILE}，跳过该文件夹")
                        print(f"    ⊙ 请在 {folder} 中创建 {PR_DESCRIPTION_FILE} 文件")
                        print()
                        continue

            # 创建分支
            if is_main:
                # main 分支使用孤儿分支（作为基础）
                run(["git", "checkout", "--orphan", branch], tmp_dir)
            else:
                # 其他分支基于远程 main 分支创建（这样可以创建PR）
                try:
                    # 尝试从远程 main 分支创建新分支
                    run(["git", "checkout", "-b", branch, f"origin/{args.main_name}"], tmp_dir)
                except subprocess.CalledProcessError:
                    # 如果远程 main 不存在，使用孤儿分支
                    print(f"    ⓘ 远程 {args.main_name} 分支不存在，使用孤儿分支")
                    run(["git", "checkout", "--orphan", branch], tmp_dir)

            # Clear index (in case)
            run(["git", "rm", "-rf", "--ignore-unmatch", "."], tmp_dir)

            # 上传文件夹内的所有文件
            copy_folder_to_repo_root(folder, tmp_dir)

            # Ensure git has at least one file if folder is empty
            has_any = any(tmp_dir.iterdir()) and any(p.name != ".git" for p in tmp_dir.iterdir())
            if not has_any:
                (tmp_dir / ".gitkeep").write_text("", encoding="utf-8")

            run(["git", "add", "-A"], tmp_dir)

            # Commit (if no changes, commit will fail; handle by skipping)
            # main文件夹使用特殊的commit信息
            commit_msg = "input data" if is_main else branch
            try:
                run(["git", "commit", "-m", commit_msg], tmp_dir)
            except subprocess.CalledProcessError:
                print(f"    ⊙ Skip commit (no changes) for branch: {branch}")

            push_cmd = ["git", "push", "-u", "origin", branch]
            if args.force:
                push_cmd.insert(2, "--force")
            run(push_cmd, tmp_dir)
            print(f"    ✓ Pushed branch: {branch}")

            # 创建 Pull Request（main 文件夹不创建 PR）
            if args.create_pr and not is_main:
                # 使用文件夹名作为 PR 标题
                pr_title = branch
                # 从 PR 描述文件读取 PR 描述
                pr_body = get_pr_description(folder)
                    
                # 等待一小段时间，确保分支已经推送成功
                time.sleep(1)
                
                create_pull_request(
                    repo_url=args.repo_url,
                    branch_name=branch,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    username=GITHUB_USERNAME,
                    token=GITHUB_TOKEN
                )

# ========================================
# Push-PR 命令：推送分支并创建PR
# ========================================
def cmd_push_pr(args):
    """推送分支并创建PR（自动设置 create_pr=True）"""
    print("\n" + "="*60)
    print("推送分支并创建PR")
    print("="*60 + "\n")
    
    # 强制启用 create_pr
    args.create_pr = True
    
    # 执行推送
    cmd_push(args)
    
    print("\n" + "="*60)
    print("推送和PR创建完成！")
    print("="*60 + "\n")


# ========================================
# 主程序和命令行参数解析
# ========================================
def main():
    parser = argparse.ArgumentParser(
        description="Bugbash工作流：管理文件夹、推送分支并创建PR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 创建文件夹
  python Bugbash_workflow.py create

  # 同步main文件夹内容到其他文件夹
  python Bugbash_workflow.py sync

  # 推送文件夹为分支（不创建PR）
  python Bugbash_workflow.py push

  # 推送文件夹为分支并创建PR（推荐）
  python Bugbash_workflow.py push-pr

  # 强制推送并创建PR
  python Bugbash_workflow.py push-pr --force

  # 推送特定文件夹并创建PR
  python Bugbash_workflow.py push-pr --folders folder1 folder2
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # Create 子命令
    parser_create = subparsers.add_parser(
        "create",
        help="创建模板和自定义文件夹"
    )
    parser_create.set_defaults(func=cmd_create)
    
    # Sync 子命令
    parser_sync = subparsers.add_parser(
        "sync",
        help="同步main文件夹内容到目标文件夹"
    )
    parser_sync.add_argument(
        "--main-name",
        default=MAIN_FOLDER_NAME,
        help=f"模板文件夹名称 (默认: {MAIN_FOLDER_NAME})"
    )
    parser_sync.add_argument(
        "--targets",
        nargs="*",
        default=[],
        help="目标文件夹。如果省略，自动检测所有文件夹（除了main和隐藏文件夹）"
    )
    parser_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，打印将要复制的内容但不实际执行"
    )
    parser_sync.set_defaults(func=cmd_sync)
    
    # Push 子命令
    parser_push = subparsers.add_parser(
        "push",
        help="推送每个文件夹为GitHub仓库的分支"
    )
    parser_push.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help=f"GitHub仓库URL (SSH或HTTPS). 默认: {DEFAULT_REPO_URL}"
    )
    parser_push.add_argument(
        "--folders",
        nargs="*",
        default=[],
        help="要推送的文件夹。如果省略，自动检测所有文件夹（除了main和隐藏文件夹）"
    )
    parser_push.add_argument(
        "--main-name",
        default=MAIN_FOLDER_NAME,
        help=f"模板文件夹名称，会被排除 (默认: {MAIN_FOLDER_NAME})"
    )
    parser_push.add_argument(
        "--force",
        action="store_true",
        help="强制推送（覆盖远程分支）"
    )
    parser_push.add_argument(
        "--create-pr",
        action="store_true",
        help="推送后自动创建Pull Request"
    )
    parser_push.set_defaults(func=cmd_push)
    
    # Push-PR 子命令
    parser_push_pr = subparsers.add_parser(
        "push-pr",
        help="推送文件夹为分支并自动创建PR（推荐）"
    )
    parser_push_pr.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help=f"GitHub仓库URL (SSH或HTTPS). 默认: {DEFAULT_REPO_URL}"
    )
    parser_push_pr.add_argument(
        "--folders",
        nargs="*",
        default=[],
        help="要推送的文件夹。如果省略，自动检测所有文件夹（除了main和隐藏文件夹）"
    )
    parser_push_pr.add_argument(
        "--main-name",
        default=MAIN_FOLDER_NAME,
        help=f"模板文件夹名称，会被排除 (默认: {MAIN_FOLDER_NAME})"
    )
    parser_push_pr.add_argument(
        "--force",
        action="store_true",
        help="强制推送（覆盖远程分支）"
    )
    parser_push_pr.set_defaults(func=cmd_push_pr)
    
    args = parser.parse_args()
    
    if not hasattr(args, "func"):
        parser.print_help()
        return
    
    args.func(args)


if __name__ == "__main__":
    main()
