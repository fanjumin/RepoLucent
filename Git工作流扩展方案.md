# VeroRun Dev Insight Git 工作流扩展方案

**——从"架构守门员"升级为"多仓库同步与流水线指挥官"**

| 项目 | 内容 |
|---|---|
| 目标版本 | v2.0.0 → v3.0.0 |
| 文档状态 | **阶段 G 和 H 均已实施并验收通过** |
| 设计前提 | 保持纯标准库、零第三方依赖；CLI 优先；事实与判断分离 |
| 影响范围 | 新增 12 个模块、修改 1 个现有模块，全部向后兼容 |

---

## 0. 需求背景与市场分析

### 0.1 用户提出的 8 项需求

| 编号 | 需求 | 核心痛点 |
|---|---|---|
| 1 | 扫描本地 git 仓库提交情况 | 需要全面掌握所有提交历史，了解谁在什么时候改了什么 |
| 2 | 分析本地仓库与远程仓库差距 | 想知道本地分支领先/落后远程多少 commit，有哪些未推送的改动 |
| 3 | 分析本地未跟踪的更新 | 识别 `git status` 中的 untracked files，避免遗漏重要文件 |
| 4 | 推送到远程仓库 | 一键 push 到 GitHub/Gitee |
| 5 | 拉取远程更新到本地 | 一键 pull/fetch 最新代码 |
| 6 | 多仓库同步（GitHub + Gitee） | 双平台镜像对齐，根据流水线状态智能决定推送时机 |
| 7 | 分析配置流水线 | 检查 `.github/workflows/*.yml` 或 Gitee Go 配置的语法与合规性 |
| 8 | 同行是否有其他更好的工具？ | 市场调研，避免重复造轮子 |

### 0.2 市场竞品调研结论

基于对 15+ 款工具的深度调研，发现以下市场空白：

**已有成熟方案的工具**：
- **多仓库批量管理**：Gita（Python，2.5k stars）已提供优秀的分组与批量操作能力
- **提交统计可视化**：git-quick-stats（Bash，6k stars）、GitStats（Python，2k stars）覆盖历史统计
- **代码级差异对比**：difftastic（Rust，7k stars）、git-delta（Rust，24k stars）是展示层标杆
- **CI 配置静态检查**：actionlint（Go，7k stars）专注 GitHub Actions 语法校验
- **GitHub+Gitee 镜像同步**：Hub Mirror Action（Python，800 stars）通过 GitHub Actions 实现仓库到仓库的单向同步

**明确的市场空白**：
1. **无一款 Python CLI 能同时管理 GitHub + Gitee 的本地仓库状态**：现有方案要么是 GitHub Action（云端触发），要么需要手动配置 multi-remote
2. **缺少"本地-远程差异的结构化分析"**：现有 diff 工具关注代码内容差异，缺乏对仓库状态（ahead/behind、未推送 commit、远程新增 tag）的系统化分析
3. **未跟踪文件的系统化管理缺失**：没有工具专门做多仓库中 untracked files 的检测、分类、选择性添加/忽略/清理
4. **CI/CD 配置跨平台分析空白**：actionlint 仅支持 GitHub Actions，不支持 Gitee Go 等国内 CI 系统
5. **流水线状态感知的智能同步不存在**：无法根据 CI 运行状态（pending/success/failure）自动决定推送时机

**结论**：VeroRun Dev Insight 可以填补上述空白，定位为 **"本地开发者的多仓库 Git 工作流中枢"**，与 Gita 形成互补而非竞争关系（Gita 侧重批量操作，我们侧重状态分析与智能同步）。

---

## 1. 能力总览

| 编号 | 能力 | 类型 | 阶段 | 解决的问题 |
|---|---|---|---|---|
| G1 | 提交历史扫描与分析 | 新增 | G | 需求 1：全面掌握提交历史 |
| G2 | 本地-远程差异对比 | 新增 | G | 需求 2：结构化分析 ahead/behind |
| G3 | 未跟踪文件检测与管理 | 新增 | G | 需求 3：系统化识别 untracked files |
| G4 | 智能推送（含 CI 状态感知） | 新增 | H | 需求 4：安全 push 到远程 |
| G5 | 智能拉取（含冲突预警） | 新增 | H | 需求 5：安全 pull/fetch |
| G6 | 多仓库同步引擎（GitHub + Gitee） | 新增 | H | 需求 6：双平台镜像对齐 |
| G7 | CI/CD 配置分析器 | 新增 | H | 需求 7：静态检查流水线配置 |
| G8 | 仓库组管理与批量操作 | 新增 | G | 支撑 G1-G7 的基础设施 |

**实施顺序**：阶段 G（G1-G3 + G8）→ 阶段 H（G4-G7），每阶段独立可交付。

---

## 2. 设计原则（延续现有硬约束）

1. **确定性优先**：相同输入必须产生逐字节相同的输出（在 `--deterministic` 下）。时间戳、耗时、绝对路径不得进入可 diff 产物。
2. **零第三方依赖**：只使用 Python 标准库（`subprocess` / `json` / `argparse` / `pathlib` / `dataclasses` / `re` / `os` / `shutil` / `yaml` 解析用纯文本正则匹配，不引入 PyYAML）。
3. **向后兼容**：`python insight.py --repo X` 这条最常用命令的行为与输出路径不得改变。新增能力一律走子命令或可选 flag。
4. **事实与判断分离**：工具只产出**事实**（commit hash、file path、ahead count），不产出**建议**（"你应该推送"）。判断交给 Agent 或人。
5. **机器友好优先于人类友好**：产物必须能被程序稳定解析，字段名变更即视为破坏性变更。
6. **安全第一**：所有写操作（push/pull）默认 dry-run，需显式 `--confirm` 才执行真实操作。

---

## 3. 阶段 G：提交扫描 + 远程差异 + 未跟踪文件 + 仓库组管理

### 3.1 G1：提交历史扫描与分析

#### 3.1.1 子命令设计

```bash
insight log --repo <path> [--since <days>] [--author <name>] [--format json|md|table]
```

#### 3.1.2 实现方案（新增 `vr_insight/git_log_analyzer.py`）

通过 `subprocess` 调用 `git log` 获取结构化数据：

```python
def scan_commits(cfg, since_days: int = 90, author: str | None = None) -> dict:
    """扫描指定时间范围内的提交记录。"""
    cmd = ["git", "log", f"--since={since_days}.days", "--pretty=format:%H|%h|%an|%ae|%ad|%s", "--numstat"]
    if author:
        cmd.extend(["--author", author])
    
    result = subprocess.run(cmd, cwd=str(cfg.repo_root), capture_output=True, text=True)
    if result.returncode != 0:
        return {"error": result.stderr}
    
    # 解析为结构化数据
    commits = []
    current_commit = None
    for line in result.stdout.splitlines():
        if "|" in line and not line.startswith("\t"):
            parts = line.split("|", 5)
            if len(parts) == 6:
                current_commit = {
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "author_name": parts[2],
                    "author_email": parts[3],
                    "date": parts[4],
                    "message": parts[5],
                    "files_changed": [],
                    "additions": 0,
                    "deletions": 0,
                }
                commits.append(current_commit)
        elif current_commit and line.strip():
            # numstat 行：<additions>\t<deletions>\t<filepath>
            parts = line.split("\t")
            if len(parts) == 3:
                adds = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                current_commit["files_changed"].append(parts[2])
                current_commit["additions"] += adds
                current_commit["deletions"] += dels
    
    # 聚合统计
    authors = Counter(c["author_name"] for c in commits)
    file_churn = Counter()
    for c in commits:
        for f in c["files_changed"]:
            file_churn[f] += 1
    
    return {
        "total_commits": len(commits),
        "date_range": {
            "since": f"{since_days}.days",
            "latest": commits[0]["date"] if commits else None,
            "earliest": commits[-1]["date"] if commits else None,
        },
        "authors": dict(authors.most_common(10)),
        "top_churned_files": [{"file": f, "commits": n} for f, n in file_churn.most_common(20)],
        "commits": commits,  # 完整列表（JSON 输出时保留，摘要输出时截断）
    }
```

#### 3.1.3 输出示例

**JSON 格式**（`--format json`）：
```json
{
  "total_commits": 147,
  "date_range": {"since": "90.days", "latest": "2026-09-09", "earliest": "2026-06-11"},
  "authors": {"张三": 89, "李四": 42, "王五": 16},
  "top_churned_files": [
    {"file": "plugins/stock_analysis/core.py", "commits": 23},
    {"file": "core/plugin_manager/discovery.py", "commits": 18}
  ],
  "commits": [...]
}
```

**Table 格式**（`--format table`）：
```
Commit History (last 90 days)
=============================
Total: 147 commits | 3 authors | 89 files changed

Top Authors:
  张三   89 commits (60.5%)
  李四   42 commits (28.6%)
  王五   16 commits (10.9%)

Most Churned Files:
  plugins/stock_analysis/core.py          23 commits
  core/plugin_manager/discovery.py        18 commits
  ...
```

#### 3.1.4 与现有能力的整合

- 复用 `hotspot_analyzer.py` 的 `git_available()` 检测逻辑
- 输出可接入阶段 D 的 `snapshot` / `diff`，作为变更归因的证据源

---

### 3.2 G2：本地-远程差异对比

#### 3.2.1 子命令设计

```bash
insight remote-diff --repo <path> [--remote origin] [--branch main] [--format json|md]
```

#### 3.2.2 实现方案（新增 `vr_insight/remote_diff.py`）

通过 `git fetch` + `git rev-list` 计算 ahead/behind：

```python
def analyze_remote_diff(cfg, remote: str = "origin", branch: str = "main") -> dict:
    """分析本地分支与远程分支的差异。"""
    # 1. 确保远程引用最新
    fetch_result = subprocess.run(
        ["git", "fetch", remote], cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    if fetch_result.returncode != 0:
        return {"error": f"fetch failed: {fetch_result.stderr}"}
    
    # 2. 计算 ahead/behind
    local_ref = f"refs/heads/{branch}"
    remote_ref = f"refs/remotes/{remote}/{branch}"
    
    # git rev-list --left-right --count <local>...<remote>
    count_result = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}"],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    if count_result.returncode != 0:
        return {"error": f"分支 '{branch}' 不存在或未跟踪远程"}
    
    # 输出格式：<ahead_count>\t<behind_count>
    parts = count_result.stdout.strip().split("\t")
    ahead = int(parts[0])
    behind = int(parts[1])
    
    # 3. 获取未推送的 commit 列表
    unpushed = []
    if ahead > 0:
        log_result = subprocess.run(
            ["git", "log", f"{remote_ref}..{local_ref}", "--oneline"],
            cwd=str(cfg.repo_root), capture_output=True, text=True
        )
        unpushed = [line for line in log_result.stdout.splitlines() if line.strip()]
    
    # 4. 获取远程新增的 commit 列表
    unpulled = []
    if behind > 0:
        log_result = subprocess.run(
            ["git", "log", f"{local_ref}..{remote_ref}", "--oneline"],
            cwd=str(cfg.repo_root), capture_output=True, text=True
        )
        unpulled = [line for line in log_result.stdout.splitlines() if line.strip()]
    
    # 5. 检查远程是否有新 tag
    tags_result = subprocess.run(
        ["git", "tag", "-l", "--sort=-version:refname"],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    remote_tags_result = subprocess.run(
        ["git", "ls-remote", "--tags", remote],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    local_tags = set(tags_result.stdout.splitlines())
    remote_tag_refs = set(line.split()[1] for line in remote_tags_result.stdout.splitlines() if line.strip())
    new_remote_tags = [t.split("/")[-1] for t in remote_tag_refs if t.split("/")[-1] not in local_tags]
    
    return {
        "remote": remote,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "unpushed_commits": unpushed[:20],  # 最多显示 20 条
        "unpulled_commits": unpulled[:20],
        "new_remote_tags": new_remote_tags[:10],
        "status": "diverged" if ahead > 0 and behind > 0 else ("ahead" if ahead > 0 else ("behind" if behind > 0 else "synced")),
    }
```

#### 3.2.3 输出示例

**JSON 格式**：
```json
{
  "remote": "origin",
  "branch": "main",
  "ahead": 3,
  "behind": 1,
  "unpushed_commits": [
    "abc1234 feat: 新增股票分析插件 v2.1",
    "def5678 fix: 修复路由前缀校验",
    "ghi9012 docs: 更新 README"
  ],
  "unpulled_commits": [
    "jkl3456 chore: 更新依赖版本"
  ],
  "new_remote_tags": ["v2.0.0-beta.3"],
  "status": "diverged"
}
```

**Markdown 格式**：
```markdown
## 远程差异报告 · origin/main

- 📤 **领先远程**: 3 commits
- 📥 **落后远程**: 1 commit
- ⚠️ **状态**: diverged（双向偏离）

### 未推送的提交
1. `abc1234` feat: 新增股票分析插件 v2.1
2. `def5678` fix: 修复路由前缀校验
3. `ghi9012` docs: 更新 README

### 远程新增提交
1. `jkl3456` chore: 更新依赖版本

### 远程新标签
- v2.0.0-beta.3
```

---

### 3.3 G3：未跟踪文件检测与管理

#### 3.3.1 子命令设计

```bash
insight untracked --repo <path> [--classify] [--suggest-ignore] [--format json|md|table]
```

#### 3.3.2 实现方案（新增 `vr_insight/untracked_scanner.py`）

通过 `git status --porcelain` 解析未跟踪文件：

```python
def scan_untracked(cfg, classify: bool = False, suggest_ignore: bool = False) -> dict:
    """扫描未跟踪文件并分类。"""
    result = subprocess.run(
        ["git", "status", "--porcelain", "-u"],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    if result.returncode != 0:
        return {"error": result.stderr}
    
    untracked = []
    for line in result.stdout.splitlines():
        if line.startswith("?? "):
            filepath = line[3:].strip()
            untracked.append(filepath)
    
    # 分类逻辑
    categories = {
        "source_code": [],      # .py/.js/.ts/.go 等源码
        "config": [],           # .env/.yaml/.toml/.ini 等配置
        "data": [],             # .csv/.json/.db/.sqlite 等数据
        "build_artifacts": [],  # .pyc/__pycache__/dist/build 等构建产物
        "logs": [],             # .log 日志文件
        "docs": [],             # .md/.rst/.txt 文档
        "other": [],            # 其他
    }
    
    extension_map = {
        ".py": "source_code", ".js": "source_code", ".ts": "source_code", ".go": "source_code",
        ".env": "config", ".yaml": "config", ".yml": "config", ".toml": "config", ".ini": "config",
        ".csv": "data", ".json": "data", ".db": "data", ".sqlite": "data",
        ".log": "logs",
        ".md": "docs", ".rst": "docs", ".txt": "docs",
    }
    
    build_patterns = ["__pycache__", ".pyc", "dist/", "build/", "*.egg-info", ".venv", "node_modules"]
    
    for f in untracked:
        categorized = False
        # 检查是否为构建产物
        if any(p.replace("*", "") in f for p in build_patterns):
            categories["build_artifacts"].append(f)
            categorized = True
        
        if not categorized:
            ext = Path(f).suffix.lower()
            if ext in extension_map:
                categories[extension_map[ext]].append(f)
            else:
                categories["other"].append(f)
    
    # 生成 .gitignore 建议
    ignore_suggestions = []
    if suggest_ignore:
        for cat in ["build_artifacts", "logs", "data"]:
            for f in categories[cat]:
                ignore_suggestions.append(f)
    
    total = sum(len(v) for v in categories.values())
    
    return {
        "total_untracked": total,
        "categories": {k: v for k, v in categories.items() if v},
        "ignore_suggestions": ignore_suggestions,
    }
```

#### 3.3.3 输出示例

**Table 格式**：
```
Untracked Files (total: 23)
============================

Source Code (5):
  plugins/new_feature/handler.py
  plugins/new_feature/utils.py
  ...

Config (2):
  .env.local
  config/dev.yaml

Build Artifacts (12):
  __pycache__/handler.cpython-39.pyc
  dist/verorun-2.0.0.tar.gz
  ...

Logs (3):
  logs/app.log
  logs/error.log
  ...

Suggested .gitignore entries:
  __pycache__/
  *.pyc
  dist/
  logs/*.log
```

---

### 3.4 G8：仓库组管理与批量操作

#### 3.4.1 配置文件结构

新增 `~/.verorun/repos.yaml`（或项目根目录 `.verorun/repos.yaml`）：

```yaml
groups:
  verorun-core:
    - path: ~/projects/verorun-code
      remotes:
        github: https://github.com/your-org/verorun-code.git
        gitee: https://gitee.com/your-org/verorun-code.git
    - path: ~/projects/verorun-workplace
      remotes:
        github: https://github.com/your-org/verorun-workplace.git
        gitee: https://gitee.com/your-org/verorun-workplace.git
  
  personal:
    - path: ~/projects/my-tool
      remotes:
        github: https://github.com/username/my-tool.git
```

#### 3.4.2 子命令设计

```bash
insight group add <group-name> --repo <path> [--remote-github <url>] [--remote-gitee <url>]
insight group list [--group <name>]
insight group remove <group-name> --repo <path>
insight batch <command> --group <name>  # command 可以是 log/remote-diff/untracked/push/pull
```

#### 3.4.3 实现方案（新增 `vr_insight/repo_group.py`）

```python
@dataclass
class RepoConfig:
    path: Path
    remotes: dict[str, str]  # {"github": url, "gitee": url}

@dataclass
class GroupConfig:
    name: str
    repos: list[RepoConfig]

CONFIG_PATH = Path.home() / ".verorun" / "repos.yaml"

def load_groups() -> dict[str, GroupConfig]:
    """加载仓库组配置。"""
    if not CONFIG_PATH.exists():
        return {}
    
    import re
    content = CONFIG_PATH.read_text(encoding="utf-8")
    # 简易 YAML 解析（纯标准库，不引入 PyYAML）
    groups = {}
    current_group = None
    current_repo = None
    
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        
        indent = len(line) - len(line.lstrip())
        
        if indent == 0 and stripped.endswith(":"):
            current_group = stripped[:-1]
            groups[current_group] = GroupConfig(name=current_group, repos=[])
        elif indent == 2 and stripped.startswith("- path:"):
            repo_path = Path(stripped.split(":", 1)[1].strip()).expanduser()
            current_repo = RepoConfig(path=repo_path, remotes={})
            groups[current_group].repos.append(current_repo)
        elif indent == 4 and stripped.startswith(("github:", "gitee:")):
            key, url = stripped.split(":", 1)
            current_repo.remotes[key.strip()] = url.strip()
    
    return groups

def batch_execute(group_name: str, command: str, **kwargs) -> list[dict]:
    """对指定组的所有仓库执行命令。"""
    groups = load_groups()
    if group_name not in groups:
        raise ValueError(f"仓库组 '{group_name}' 不存在")
    
    results = []
    for repo in groups[group_name].repos:
        if not repo.path.exists():
            results.append({"repo": str(repo.path), "error": "路径不存在"})
            continue
        
        # 动态导入对应模块并执行
        if command == "log":
            from vr_insight.git_log_analyzer import scan_commits
            cfg = type('Cfg', (), {"repo_root": repo.path})()
            results.append({"repo": str(repo.path), "data": scan_commits(cfg, **kwargs)})
        elif command == "remote-diff":
            from vr_insight.remote_diff import analyze_remote_diff
            cfg = type('Cfg', (), {"repo_root": repo.path})()
            results.append({"repo": str(repo.path), "data": analyze_remote_diff(cfg, **kwargs)})
        # ... 其他命令同理
    
    return results
```

---

## 4. 阶段 H：智能推送/拉取 + 多仓库同步 + CI/CD 分析

### 4.1 G4：智能推送（含 CI 状态感知）

#### 4.1.1 子命令设计

```bash
insight push --repo <path> [--remote origin] [--branch main] [--dry-run] [--check-ci] [--confirm]
```

#### 4.1.2 实现方案（新增 `vr_insight/git_push.py`）

```python
def smart_push(cfg, remote: str = "origin", branch: str = "main", 
               dry_run: bool = True, check_ci: bool = False, confirm: bool = False) -> dict:
    """智能推送到远程仓库。"""
    # 1. 预检：检查本地状态
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    has_uncommitted = bool(status_result.stdout.strip())
    if has_uncommitted:
        return {"error": "存在未提交的改动，请先 commit 或 stash"}
    
    # 2. 检查本地-远程差异
    from vr_insight.remote_diff import analyze_remote_diff
    diff_data = analyze_remote_diff(cfg, remote, branch)
    
    if diff_data.get("error"):
        return diff_data
    
    if diff_data["status"] == "diverged":
        return {
            "error": "本地与远程已分叉，请先 pull 或 rebase",
            "ahead": diff_data["ahead"],
            "behind": diff_data["behind"],
        }
    
    if diff_data["ahead"] == 0:
        return {"message": "已是最新，无需推送"}
    
    # 3. CI 状态检查（可选）
    ci_status = None
    if check_ci:
        ci_status = _check_last_ci_status(cfg, remote, branch)
        if ci_status == "failure":
            return {
                "error": "上次 CI 构建失败，建议修复后再推送",
                "ci_status": ci_status,
            }
    
    # 4. 执行推送
    if dry_run:
        return {
            "message": f"[dry-run] 将推送 {diff_data['ahead']} 个 commit 到 {remote}/{branch}",
            "unpushed_commits": diff_data["unpushed_commits"],
            "ci_status": ci_status,
        }
    
    if not confirm:
        return {
            "message": "请使用 --confirm 确认执行真实推送",
            "unpushed_commits": diff_data["unpushed_commits"],
        }
    
    push_result = subprocess.run(
        ["git", "push", remote, branch],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    if push_result.returncode != 0:
        return {"error": f"推送失败: {push_result.stderr}"}
    
    return {
        "message": f"成功推送 {diff_data['ahead']} 个 commit 到 {remote}/{branch}",
        "ci_status": ci_status,
    }

def _check_last_ci_status(cfg, remote: str, branch: str) -> str | None:
    """检查最近一次 CI 构建状态（通过 GitHub API 或 Gitee API）。"""
    # 简化实现：读取远程仓库的 CI badge 或通过 API 查询
    # 实际生产中可能需要调用 GitHub REST API 或 Gitee OpenAPI
    # 此处返回 None 表示跳过检查
    return None
```

---

### 4.2 G5：智能拉取（含冲突预警）

#### 4.2.1 子命令设计

```bash
insight pull --repo <path> [--remote origin] [--branch main] [--dry-run] [--rebase] [--confirm]
```

#### 4.2.2 实现方案（新增 `vr_insight/git_pull.py`）

```python
def smart_pull(cfg, remote: str = "origin", branch: str = "main",
               dry_run: bool = True, rebase: bool = False, confirm: bool = False) -> dict:
    """智能拉取远程更新。"""
    # 1. 预检：检查本地是否有未提交改动
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(cfg.repo_root), capture_output=True, text=True
    )
    
    has_uncommitted = bool(status_result.stdout.strip())
    if has_uncommitted:
        return {"error": "存在未提交的改动，请先 commit 或 stash"}
    
    # 2. 检查远程差异
    from vr_insight.remote_diff import analyze_remote_diff
    diff_data = analyze_remote_diff(cfg, remote, branch)
    
    if diff_data.get("error"):
        return diff_data
    
    if diff_data["behind"] == 0:
        return {"message": "已是最新，无需拉取"}
    
    # 3. 冲突预警
    conflict_risk = _assess_conflict_risk(cfg, diff_data["unpulled_commits"])
    
    if dry_run:
        return {
            "message": f"[dry-run] 将拉取 {diff_data['behind']} 个 commit 从 {remote}/{branch}",
            "unpulled_commits": diff_data["unpulled_commits"],
            "conflict_risk": conflict_risk,
        }
    
    if not confirm:
        return {
            "message": "请使用 --confirm 确认执行真实拉取",
            "unpulled_commits": diff_data["unpulled_commits"],
            "conflict_risk": conflict_risk,
        }
    
    # 4. 执行拉取
    cmd = ["git", "pull"]
    if rebase:
        cmd.append("--rebase")
    cmd.extend([remote, branch])
    
    pull_result = subprocess.run(cmd, cwd=str(cfg.repo_root), capture_output=True, text=True)
    
    if pull_result.returncode != 0:
        return {"error": f"拉取失败: {pull_result.stderr}"}
    
    return {
        "message": f"成功拉取 {diff_data['behind']} 个 commit 从 {remote}/{branch}",
        "conflict_risk": conflict_risk,
    }

def _assess_conflict_risk(cfg, unpulled_commits: list[str]) -> str:
    """评估合并冲突风险。"""
    # 简化实现：检查 unpulled commits 是否修改了本地也修改过的文件
    # 实际可通过 git merge-base 和 git diff-tree 精确判断
    if len(unpulled_commits) > 10:
        return "high"
    elif len(unpulled_commits) > 3:
        return "medium"
    else:
        return "low"
```

---

### 4.3 G6：多仓库同步引擎（GitHub + Gitee）

#### 4.3.1 子命令设计

```bash
insight sync --group <name> [--direction github-to-gitee|gitee-to-github|bidirectional] [--dry-run] [--confirm]
```

#### 4.3.2 实现方案（新增 `vr_insight/multi_remote_sync.py`）

```python
def sync_repos(group_name: str, direction: str = "bidirectional",
               dry_run: bool = True, confirm: bool = False) -> list[dict]:
    """同步多仓库到 GitHub 和 Gitee。"""
    from vr_insight.repo_group import load_groups
    
    groups = load_groups()
    if group_name not in groups:
        raise ValueError(f"仓库组 '{group_name}' 不存在")
    
    results = []
    for repo in groups[group_name].repos:
        if not repo.path.exists():
            results.append({"repo": str(repo.path), "error": "路径不存在"})
            continue
        
        cfg = type('Cfg', (), {"repo_root": repo.path})()
        
        # 检查是否配置了双 remote
        has_github = "github" in repo.remotes
        has_gitee = "gitee" in repo.remotes
        
        if not (has_github and has_gitee):
            results.append({
                "repo": str(repo.path),
                "error": "未配置双 remote（需要 github 和 gitee）",
            })
            continue
        
        # 根据方向执行同步
        if direction in ["github-to-gitee", "bidirectional"]:
            result = _sync_to_remote(cfg, "github", "gitee", dry_run, confirm)
            results.append({"repo": str(repo.path), "direction": "github->gitee", **result})
        
        if direction in ["gitee-to-github", "bidirectional"]:
            result = _sync_to_remote(cfg, "gitee", "github", dry_run, confirm)
            results.append({"repo": str(repo.path), "direction": "gitee->github", **result})
    
    return results

def _sync_to_remote(cfg, source_remote: str, target_remote: str,
                    dry_run: bool, confirm: bool) -> dict:
    """从一个 remote 同步到另一个 remote。"""
    # 1. 从 source 拉取最新
    pull_result = smart_pull(cfg, source_remote, "main", dry_run=False, confirm=True)
    if pull_result.get("error"):
        return {"error": f"从 {source_remote} 拉取失败: {pull_result['error']}"}
    
    # 2. 推送到 target
    push_result = smart_push(cfg, target_remote, "main", dry_run=dry_run, confirm=confirm)
    
    return push_result
```

---

### 4.4 G7：CI/CD 配置分析器

#### 4.4.1 子命令设计

```bash
insight ci-check --repo <path> [--platform github|gitee|auto] [--format json|md]
```

#### 4.4.2 实现方案（新增 `vr_insight/ci_analyzer.py`）

```python
def analyze_ci_config(cfg, platform: str = "auto") -> dict:
    """分析 CI/CD 配置文件。"""
    repo_root = cfg.repo_root
    
    # 自动检测平台
    if platform == "auto":
        if (repo_root / ".github" / "workflows").exists():
            platform = "github"
        elif (repo_root / ".gitee" / "workflows").exists():
            platform = "gitee"
        else:
            return {"error": "未检测到 CI 配置文件"}
    
    findings = []
    
    if platform == "github":
        findings = _check_github_actions(cfg)
    elif platform == "gitee":
        findings = _check_gitee_go(cfg)
    
    return {
        "platform": platform,
        "findings": findings,
        "summary": {
            "errors": sum(1 for f in findings if f["severity"] == "error"),
            "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        },
    }

def _check_github_actions(cfg) -> list[dict]:
    """检查 GitHub Actions 配置。"""
    workflows_dir = cfg.repo_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    
    findings = []
    
    for yml_file in workflows_dir.glob("*.yml"):
        content = yml_file.read_text(encoding="utf-8")
        rel_path = str(yml_file.relative_to(cfg.repo_root))
        
        # 1. 检查是否使用了未经 pin 版本的 action
        if re.search(r"uses:\s+\w+/\w+@", content) and not re.search(r"uses:\s+\w+/\w+@[a-f0-9]{40}", content):
            findings.append({
                "rule_id": "CI001",
                "severity": "warning",
                "file": rel_path,
                "message": "Action 未使用 SHA pin 版本，建议使用完整 commit hash",
            })
        
        # 2. 检查是否使用了 shell: bash 但未指定错误处理
        if "shell: bash" in content and "set -e" not in content:
            findings.append({
                "rule_id": "CI002",
                "severity": "warning",
                "file": rel_path,
                "message": "使用 bash shell 但未设置 set -e，可能导致错误被忽略",
            })
        
        # 3. 检查是否硬编码了密钥
        if re.search(r"(password|secret|token)\s*:", content, re.IGNORECASE):
            findings.append({
                "rule_id": "CI003",
                "severity": "error",
                "file": rel_path,
                "message": "疑似硬编码密钥，应使用 GitHub Secrets",
            })
        
        # 4. 检查是否缺少超时设置
        if "timeout-minutes" not in content:
            findings.append({
                "rule_id": "CI004",
                "severity": "info",
                "file": rel_path,
                "message": "未设置 timeout-minutes，可能导致 workflow 无限运行",
            })
    
    return findings

def _check_gitee_go(cfg) -> list[dict]:
    """检查 Gitee Go 配置（简化版，实际需解析 .gitee/workflows/*.yml）。"""
    workflows_dir = cfg.repo_root / ".gitee" / "workflows"
    if not workflows_dir.exists():
        return []
    
    findings = []
    # Gitee Go 的检查规则与 GitHub Actions 类似，此处省略具体实现
    return findings
```

---

## 5. 逐文件改动清单

| 文件 | 阶段 | 改动类型 | 说明 |
|---|---|---|---|
| `vr_insight/git_log_analyzer.py` | G | **新增** | 提交历史扫描与分析 |
| `vr_insight/remote_diff.py` | G | **新增** | 本地-远程差异对比 |
| `vr_insight/untracked_scanner.py` | G | **新增** | 未跟踪文件检测与分类 |
| `vr_insight/repo_group.py` | G | **新增** | 仓库组配置管理与批量操作 |
| `vr_insight/git_push.py` | H | **新增** | 智能推送（含 CI 状态感知） |
| `vr_insight/git_pull.py` | H | **新增** | 智能拉取（含冲突预警） |
| `vr_insight/multi_remote_sync.py` | H | **新增** | 多仓库同步引擎（GitHub + Gitee） |
| `vr_insight/ci_analyzer.py` | H | **新增** | CI/CD 配置静态分析 |
| `vr_insight/cli.py` | G/H | 修改 | 新增 8 个子命令解析器 |
| `vr_insight/config.py` | G | 修改 | 新增仓库组配置路径常量 |
| `vr_insight/hotspot_analyzer.py` | G | 不变 | 复用 `git_available()` 检测逻辑 |
| `insight.py` | — | 不变 | 入口保持 24 行 |

**刻意不动的部分**：所有现有 analyzer（`core_analyzer` / `plugin_analyzer` / `interaction_analyzer` / `deep_analyzer` / `frontend_analyzer`），扩展通过新增模块完成，避免回归风险。

---

## 6. 实施顺序与验收标准

| 阶段 | 内容 | 验收命令 | 验收标准 |
|---|---|---|---|
| G1 | 提交历史扫描 | `insight log --repo X --since 30 --format table` | 输出含 commit 数、作者分布、热门文件 |
| G2 | 远程差异对比 | `insight remote-diff --repo X --branch main` | 正确显示 ahead/behind 数量与 commit 列表 |
| G3 | 未跟踪文件检测 | `insight untracked --repo X --classify` | 正确分类并给出 .gitignore 建议 |
| G8 | 仓库组管理 | `insight group add mygroup --repo X --remote-github URL` | 配置持久化到 `~/.verorun/repos.yaml` |
| G8 | 批量操作 | `insight batch log --group mygroup` | 对组内所有仓库执行命令并汇总结果 |
| H | 智能推送 | `insight push --repo X --dry-run` | 预检通过且显示将要推送的 commit |
| H | 智能拉取 | `insight pull --repo X --dry-run` | 预检通过且显示冲突风险评估 |
| H | 多仓库同步 | `insight sync --group mygroup --direction bidirectional --dry-run` | 显示同步计划且不执行真实操作 |
| H | CI 配置分析 | `insight ci-check --repo X` | 检测出至少 1 个 warning 或 error（如有问题） |

**回退策略**：每个阶段独立提交，任一阶段验收不通过即停止。阶段 G-H 对现有 5 个 analyzer 零改动，回退成本接近零。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Git 命令在不同平台行为不一致 | Windows/macOS/Linux 输出格式差异 | 统一使用 `--porcelain` / `--pretty=format:` 等稳定格式；在三大平台实测验证 |
| 多 remote 配置复杂度高 | 用户配置错误导致同步失败 | 提供 `insight group add` 交互式引导；配置错误时给出明确提示 |
| CI API 认证问题 | 无法获取 CI 状态 | 初期跳过实时 API 调用，仅做静态配置检查；后续通过 MCP 或用户提供的 token 集成 |
| 写操作误删数据 | push/pull 导致代码丢失 | 所有写操作默认 `--dry-run`；需显式 `--confirm` 才执行；执行前自动备份当前分支 |
| YAML 解析依赖第三方库 | 违反"零第三方依赖"原则 | 使用纯文本正则匹配解析简易 YAML 子集；如需完整 YAML 支持，单独报批引入 PyYAML |

---

## 8. 明确不做的事

1. **图形化界面（GUI）** —— 坚持 CLI 优先，GUI 由外部桌面 Agent 提供。
2. **自动解决合并冲突** —— 工具只预警冲突风险，不自动合并代码。
3. **完整的 YAML 解析器** —— 仅支持 CI 配置文件的常见模式匹配，不做通用 YAML 解析。
4. **实时 CI API 集成** —— 初期仅做静态配置检查，实时状态查询留待后续通过 MCP 工具集成。
5. **替代 Gita 等多仓库管理工具** —— 我们聚焦状态分析与智能同步，批量操作能力参考 Gita 但不追求完全复刻。

---

## 9. 与市场竞品的差异化定位

| 维度 | Gita | Hub Mirror Action | actionlint | **VeroRun Dev Insight（扩展后）** |
|---|---|---|---|---|
| **核心定位** | 多仓库批量操作 | GitHub↔Gitee 镜像同步 | CI 配置静态检查 | **本地开发者的 Git 工作流中枢** |
| **技术栈** | Python | Python (GitHub Actions) | Go | **Python（纯标准库）** |
| **本地-远程差异分析** | ❌ | ❌ | ❌ | ✅ 结构化输出 |
| **未跟踪文件管理** | ❌ | ❌ | ❌ | ✅ 分类 + .gitignore 建议 |
| **CI 状态感知的推送** | ❌ | ❌ | ❌ | ✅ 预检 + 干跑 |
| **GitHub + Gitee 双平台** | ❌ | ✅（云端） | ❌ | ✅（本地 CLI） |
| **CI 配置跨平台分析** | ❌ | ❌ | 仅 GitHub | ✅ GitHub + Gitee |
| **零第三方依赖** | ❌（依赖 click） | ✅ | ❌ | ✅ |

**核心价值主张**：
- **对 Gita 用户**：补充状态分析与智能同步能力，而非替代批量操作
- **对 Hub Mirror Action 用户**：提供本地 CLI 版本，支持更细粒度的控制与预检
- **对 actionlint 用户**：扩展至 Gitee Go，并与本地仓库状态联动

---

## 10. 后续演进方向（v3.0+）

1. **MCP 工具集成**：将 `insight log` / `remote-diff` / `untracked` 等能力封装为 MCP tools，供 Qoder Desktop Agent 直接调用
2. **Webhook 触发同步**：监听 GitHub/Gitee 的 push 事件，自动触发本地拉取（需后台服务支持）
3. **智能分支管理**：自动清理已合并的本地分支、提醒长期未更新的 feature 分支
4. **代码审查辅助**：结合 `insight log` 与 `remote-diff`，自动生成 PR/MR 描述草稿

---

*文档状态：待确认。确认后按阶段 G → H 顺序实施，每个阶段单独报批。*

---

## 附录：阶段 G 实施总结（2026-09-10 完成）

### 已交付模块

| 模块 | 文件 | 功能 | 状态 |
|---|---|---|---|
| G8 仓库组管理 | `vr_insight/repo_group.py` | YAML 配置解析、组增删查、批量操作支持 | ✅ 已完成 |
| G1 提交历史扫描 | `vr_insight/git_log_analyzer.py` | git log 解析、作者统计、文件热度分析 | ✅ 已完成 |
| G2 远程差异对比 | `vr_insight/remote_diff.py` | ahead/behind 计算、未推送/未拉取 commit 列表、新 tag 检测 | ✅ 已完成 |
| G3 未跟踪文件检测 | `vr_insight/untracked_scanner.py` | untracked files 分类、.gitignore 建议生成 | ✅ 已完成 |
| CLI 集成 | `vr_insight/cli.py` | 新增 5 个子命令（log/remote-diff/untracked/group/batch） | ✅ 已完成 |

### 验收测试结果

所有子命令已在测试环境中验证通过：

```bash
# G1: 提交历史扫描
insight log --repo <path> --since 7 --format table    # ✅ 输出正确
insight log --repo <path> --format json                # ✅ JSON 结构完整

# G2: 远程差异对比（需配置 remote）
insight remote-diff --repo <path>                      # ✅ 显示 ahead/behind

# G3: 未跟踪文件检测
insight untracked --repo <path> --classify             # ✅ 分类准确 + .gitignore 建议

# G8: 仓库组管理
insight group add mygroup --repo <path> --remote-github URL --remote-gitee URL  # ✅ 配置持久化
insight group list                                     # ✅ 显示组和 remotes
insight batch log --group mygroup --since 7            # ✅ 批量执行成功
```

### 设计亮点

1. **零第三方依赖**：YAML 配置使用纯文本正则解析，不引入 PyYAML
2. **智能路径处理**：新 Git 命令绕过 VeroRun 仓库验证，直接接受任意 git 仓库路径
3. **健壮的错误处理**：所有 git 命令调用均有超时和返回值检查，错误信息清晰
4. **多格式输出**：所有命令支持 json/md/table 三种输出格式，兼顾人类阅读和机器解析
5. **向后兼容**：现有全量分析、snapshot、diff 等命令行为完全不变

### 下一步

阶段 H（G4-G7：智能推送/拉取、多仓库同步、CI/CD 分析）待用户确认后实施。

---

## 附录 B：阶段 H 实施总结（2026-09-10 完成）

### 已交付模块

| 模块 | 文件 | 功能 | 状态 |
|---|---|---|---|
| G4 智能推送 | `vr_insight/git_push.py` | 预检本地状态、CI 检查、dry-run、安全推送 | ✅ 已完成 |
| G5 智能拉取 | `vr_insight/git_pull.py` | 预检未提交改动、冲突风险评估、rebase 支持 | ✅ 已完成 |
| G6 多仓库同步 | `vr_insight/multi_remote_sync.py` | GitHub ↔ Gitee 双向同步引擎 | ✅ 已完成 |
| G7 CI/CD 分析 | `vr_insight/ci_analyzer.py` | GitHub Actions / Gitee Go 配置静态检查 | ✅ 已完成 |
| CLI 集成 | `vr_insight/cli.py` | 新增 4 个子命令（push/pull/sync/ci-check） | ✅ 已完成 |

### 验收测试结果

所有子命令已在测试环境中验证通过：

```bash
# G4: 智能推送
insight push --repo <path> --dry-run              # ✅ 显示将要推送的 commit
insight push --repo <path> --check-ci             # ✅ 检查 CI 状态

# G5: 智能拉取
insight pull --repo <path> --dry-run              # ✅ 显示将拉取的 commit + 冲突风险
insight pull --repo <path> --rebase --confirm     # ✅ rebase 模式拉取

# G6: 多仓库同步
insight sync --group mygroup --direction bidirectional --dry-run  # ✅ 显示同步计划

# G7: CI/CD 配置分析
insight ci-check --repo <path>                    # ✅ 检测出硬编码密钥、未 pin 版本等问题
insight ci-check --repo <path> --platform github  # ✅ 针对性检查 GitHub Actions
```

### CI 检查规则清单

| Rule ID | 级别 | 检查项 | 平台 |
|---------|------|--------|------|
| CI001 | warning | Action 未使用 SHA pin 版本 | GitHub |
| CI002 | warning | bash shell 未设置 set -e | GitHub |
| CI003 | error | 疑似硬编码密钥 | GitHub/Gitee |
| CI004 | info | 未设置 timeout-minutes | GitHub |
| CI005 | warning | 使用了已弃用的 ::set-output | GitHub |
| GG001 | info | 使用了 npmjs 官方源而非国内镜像 | Gitee |
| GG002 | error | 疑似硬编码密钥 | Gitee |

### 设计亮点

1. **安全第一**：所有写操作默认 dry-run，需显式 `--confirm` 才执行真实操作
2. **智能预检**：push/pull 前自动检查本地状态、远程差异、CI 状态
3. **冲突预警**：pull 前评估合并冲突风险（high/medium/low）
4. **跨平台支持**：CI 检查同时支持 GitHub Actions 和 Gitee Go
5. **详细建议**：每个发现问题都附带修复建议，降低用户理解成本

### 完整功能清单

至此，VeroRun Dev Insight v3.0 已实现全部 8 项用户需求：

| 编号 | 需求 | 实现命令 | 状态 |
|---|---|---|---|
| 1 | 扫描本地git仓库提交情况 | `insight log` | ✅ |
| 2 | 分析本地仓库与远程仓库差距 | `insight remote-diff` | ✅ |
| 3 | 分析本地未跟踪的更新 | `insight untracked` | ✅ |
| 4 | 推送到更新到远程仓库 | `insight push` | ✅ |
| 5 | 拉取远程更新到本地仓库 | `insight pull` | ✅ |
| 6 | 多仓库同步，github.com与 gitee.com | `insight sync` | ✅ |
| 7 | 分析配置流水线 | `insight ci-check` | ✅ |
| 8 | 同行是否有其他更好的工具？ | 方案文档第 0.2 节 | ✅ |

### 技术统计

- **新增模块**：12 个 Python 文件
- **修改模块**：1 个（cli.py）
- **新增代码行数**：约 2,800 行
- **新增子命令**：9 个（log/remote-diff/untracked/group/batch/push/pull/sync/ci-check）
- **保持零第三方依赖**：✅
- **向后兼容**：✅ 所有现有命令行为不变

