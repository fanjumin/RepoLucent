# -*- coding: utf-8 -*-
"""CI/CD 配置静态分析。

检查 GitHub Actions 和 Gitee Go 配置的合规性、安全性、最佳实践。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def analyze_ci_config(repo_root: Path, platform: str = "auto") -> dict[str, Any]:
    """分析 CI/CD 配置文件。

    Args:
        repo_root: 仓库根目录
        platform: CI 平台（"github" / "gitee" / "auto"）

    Returns:
        分析结果
    """
    # 自动检测平台
    if platform == "auto":
        if (repo_root / ".github" / "workflows").exists():
            platform = "github"
        elif (repo_root / ".gitee" / "workflows").exists():
            platform = "gitee"
        else:
            return {
                "error": "未检测到 CI 配置文件",
                "suggestion": "请检查 .github/workflows/ 或 .gitee/workflows/ 目录",
            }

    findings = []

    if platform == "github":
        findings = _check_github_actions(repo_root)
    elif platform == "gitee":
        findings = _check_gitee_go(repo_root)

    # 统计
    error_count = sum(1 for f in findings if f["severity"] == "error")
    warning_count = sum(1 for f in findings if f["severity"] == "warning")
    info_count = sum(1 for f in findings if f["severity"] == "info")

    return {
        "platform": platform,
        "findings": findings,
        "summary": {
            "total": len(findings),
            "errors": error_count,
            "warnings": warning_count,
            "info": info_count,
        },
    }


def _check_github_actions(repo_root: Path) -> list[dict[str, Any]]:
    """检查 GitHub Actions 配置。

    检查项：
    - CI001: Action 未使用 SHA pin 版本
    - CI002: bash shell 未设置 set -e
    - CI003: 疑似硬编码密钥
    - CI004: 未设置 timeout-minutes
    - CI005: 使用了已弃用的 set-output 命令
    """
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []

    findings = []

    for yml_file in workflows_dir.glob("*.yml"):
        try:
            content = yml_file.read_text(encoding="utf-8")
        except OSError:
            continue

        rel_path = str(yml_file.relative_to(repo_root))

        # CI001: 检查是否使用了未经 pin 版本的 action
        # 匹配 uses: owner/repo@v1.2.3 但不是完整 commit hash
        uses_pattern = r"uses:\s+\S+/\S+@([a-f0-9]{40}|\S+)"
        for match in re.finditer(uses_pattern, content):
            version = match.group(1)
            if len(version) != 40:  # 不是 40 位 hash
                findings.append({
                    "rule_id": "CI001",
                    "severity": "warning",
                    "file": rel_path,
                    "line": _get_line_number(content, match.start()),
                    "message": "Action 未使用 SHA pin 版本，建议使用完整 commit hash",
                    "suggestion": f"将 @{version} 改为完整的 40 位 commit hash",
                })

        # CI002: 检查是否使用了 shell: bash 但未指定错误处理
        if "shell: bash" in content and "set -e" not in content:
            findings.append({
                "rule_id": "CI002",
                "severity": "warning",
                "file": rel_path,
                "message": "使用 bash shell 但未设置 set -e，可能导致错误被忽略",
                "suggestion": "在 run 步骤开头添加 `set -e` 或使用 `shell: bash -eo pipefail`",
            })

        # CI003: 检查是否硬编码了密钥
        secret_patterns = [
            r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?\w+",
            r"(?i)(secret|token|api_key|apikey)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{16,}",
            r"(?i)(access_token)\s*[:=]\s*['\"]?ghp_[a-zA-Z0-9]{36}",
        ]
        for pattern in secret_patterns:
            for match in re.finditer(pattern, content):
                # 排除注释行
                line_start = content.rfind("\n", 0, match.start()) + 1
                line = content[line_start:match.start()].strip()
                if not line.startswith("#"):
                    findings.append({
                        "rule_id": "CI003",
                        "severity": "error",
                        "file": rel_path,
                        "line": _get_line_number(content, match.start()),
                        "message": "疑似硬编码密钥，应使用 GitHub Secrets",
                        "suggestion": "使用 ${{ secrets.SECRET_NAME }} 替代硬编码值",
                    })
                    break  # 每个文件只报告一次

        # CI004: 检查是否缺少超时设置
        if "timeout-minutes" not in content:
            findings.append({
                "rule_id": "CI004",
                "severity": "info",
                "file": rel_path,
                "message": "未设置 timeout-minutes，可能导致 workflow 无限运行",
                "suggestion": "在 job 级别添加 timeout-minutes: 30",
            })

        # CI005: 检查是否使用了已弃用的 set-output 命令
        if "::set-output" in content:
            findings.append({
                "rule_id": "CI005",
                "severity": "warning",
                "file": rel_path,
                "message": "使用了已弃用的 ::set-output 命令",
                "suggestion": "改用 $GITHUB_OUTPUT 环境变量",
            })

    return findings


def _check_gitee_go(repo_root: Path) -> list[dict[str, Any]]:
    """检查 Gitee Go 配置。

    检查项与 GitHub Actions 类似，但针对 Gitee Go 的语法特点。
    """
    workflows_dir = repo_root / ".gitee" / "workflows"
    if not workflows_dir.exists():
        return []

    findings = []

    for yml_file in workflows_dir.glob("*.yml"):
        try:
            content = yml_file.read_text(encoding="utf-8")
        except OSError:
            continue

        rel_path = str(yml_file.relative_to(repo_root))

        # Gitee Go 特有的检查项
        # GG001: 检查是否使用了正确的镜像源
        if "registry.npmjs.org" in content:
            findings.append({
                "rule_id": "GG001",
                "severity": "info",
                "file": rel_path,
                "message": "使用了 npmjs 官方源，建议切换到国内镜像加速",
                "suggestion": "使用 registry.npmmirror.com 或 Gitee 提供的镜像",
            })

        # GG002: 检查是否硬编码了密钥（同 GitHub）
        secret_patterns = [
            r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?\w+",
            r"(?i)(secret|token|api_key)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{16,}",
        ]
        for pattern in secret_patterns:
            for match in re.finditer(pattern, content):
                line_start = content.rfind("\n", 0, match.start()) + 1
                line = content[line_start:match.start()].strip()
                if not line.startswith("#"):
                    findings.append({
                        "rule_id": "GG002",
                        "severity": "error",
                        "file": rel_path,
                        "line": _get_line_number(content, match.start()),
                        "message": "疑似硬编码密钥，应使用 Gitee 变量",
                        "suggestion": "使用 ${{ vars.SECRET_NAME }} 替代硬编码值",
                    })
                    break

    return findings


def _get_line_number(content: str, pos: int) -> int:
    """获取字符位置对应的行号。"""
    return content[:pos].count("\n") + 1


def format_ci_findings(data: dict[str, Any]) -> str:
    """将 CI 检查结果格式化为可读文本。

    Args:
        data: analyze_ci_config 返回的数据

    Returns:
        格式化的字符串
    """
    if "error" in data:
        return f"错误: {data['error']}\n建议: {data.get('suggestion', '')}"

    lines = []
    lines.append(f"CI/CD 配置分析报告 ({data['platform']})")
    lines.append("=" * 60)

    summary = data["summary"]
    lines.append(f"\n总计: {summary['total']} 个问题")
    lines.append(f"  ❌ 错误: {summary['errors']}")
    lines.append(f"  ⚠️  警告: {summary['warnings']}")
    lines.append(f"  ℹ️  提示: {summary['info']}")

    if not data["findings"]:
        lines.append("\n✅ 未发现任何问题！")
        return "\n".join(lines)

    # 按严重程度分组
    severity_order = {"error": 0, "warning": 1, "info": 2}
    sorted_findings = sorted(
        data["findings"],
        key=lambda f: (severity_order.get(f["severity"], 3), f["rule_id"])
    )

    current_severity = None
    for finding in sorted_findings:
        if finding["severity"] != current_severity:
            current_severity = finding["severity"]
            severity_label = {
                "error": "❌ 错误",
                "warning": "⚠️  警告",
                "info": "ℹ️  提示",
            }.get(current_severity, current_severity)
            lines.append(f"\n{severity_label}:")

        line_info = f":{finding['line']}" if finding.get("line") else ""
        lines.append(f"  [{finding['rule_id']}] {finding['file']}{line_info}")
        lines.append(f"    {finding['message']}")
        if finding.get("suggestion"):
            lines.append(f"    💡 {finding['suggestion']}")

    return "\n".join(lines)
