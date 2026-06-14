#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P7: Bootstrap — 零起始Skill免疫系统初始化 (OpenSkill / Yunjue Agent 蒸馏).

v1.2 (2026-06-13): 蒸馏自 OpenSkill (arXiv 2606.06741) + Yunjue Agent.

核心洞见:
  OpenSkill 证明了你可以从零精调数据、零成功轨迹、零验证信号开始——
  只要能从开放世界获取锚点知识(docs/repos/web)。
  Yunjue Agent 证明了从空工具库 bootstrap 通用能力是可行的。

Bootstrap 流程:
  1. 分析新 skill 的文件结构 (file types, languages, dependencies)
  2. 从 patterns.md 库中匹配常见 anti-pattern
  3. 生成初始 patterns.md 模板
  4. 生成初始 known-issues.md 模板
  5. 生成初始 scanner 规则 (probation mode)
  6. 写入 manifest 中的 immune_system_status
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── File analysis ──────────────────────────────────────────────────────────────

def analyze_skill_structure(skill_name: str) -> dict:
    """Analyze a skill's file structure to determine its type and needs.

    Returns {file_types, languages, has_tests, complexity, dependencies}.
    """
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    root = None
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            root = d
            break

    if not root:
        return {"error": f"技能 '{skill_name}' 不存在"}

    analysis = {
        "skill_name": skill_name,
        "total_files": 0,
        "file_types": {},
        "languages": set(),
        "has_manifest": False,
        "has_skill_md": False,
        "has_constitution": False,
        "has_engine": False,
        "has_scripts": False,
        "has_tests": False,
        "complexity": "simple",  # simple | moderate | complex
    }

    for fp in root.rglob("*"):
        if fp.is_file() and "__pycache__" not in str(fp) and ".pyc" not in fp.suffix:
            analysis["total_files"] += 1
            suffix = fp.suffix or "no-ext"
            analysis["file_types"][suffix] = analysis["file_types"].get(suffix, 0) + 1

            # Detect language
            if suffix == ".py":
                analysis["languages"].add("python")
            elif suffix in (".js", ".ts", ".jsx", ".tsx"):
                analysis["languages"].add("javascript")
            elif suffix in (".sh", ".bash"):
                analysis["languages"].add("shell")
            elif suffix == ".md":
                analysis["languages"].add("markdown")
            elif suffix == ".json":
                analysis["languages"].add("json")

    analysis["languages"] = list(analysis["languages"])

    # Structural checks
    analysis["has_manifest"] = (root / "manifest.json").exists()
    analysis["has_skill_md"] = (root / "skill.md").exists() or (root / "SKILL.md").exists()
    analysis["has_constitution"] = (root / "constitution.md").exists()
    analysis["has_engine"] = (root / "engine").is_dir()
    analysis["has_scripts"] = (root / "scripts").is_dir()
    analysis["has_tests"] = (root / "tests").is_dir() or (root / "test").is_dir()

    # Complexity
    if analysis["total_files"] > 20:
        analysis["complexity"] = "complex"
    elif analysis["total_files"] > 8:
        analysis["complexity"] = "moderate"

    # Extract dependencies from manifest
    if analysis["has_manifest"]:
        try:
            mf = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            deps = mf.get("dependencies", {})
            analysis["dependencies"] = deps.get("runtime", []) + deps.get("shell_tools", [])
        except (json.JSONDecodeError, KeyError):
            analysis["dependencies"] = []

    return analysis


# ── Pattern matching ───────────────────────────────────────────────────────────

PATTERN_TEMPLATES = {
    "python": [
        {"name": "missing-utf8-header", "severity": "critical",
         "description": "Python 脚本缺少 UTF-8 stdout 头",
         "fix": "添加 import sys; sys.stdout.reconfigure(encoding='utf-8')"},
        {"name": "hardcoded-versions", "severity": "warning",
         "description": "硬编码版本约束——应从 manifest.json 动态读取",
         "fix": "从 manifest.json frontmatter 读取版本约束"},
        {"name": "missing-docstring", "severity": "info",
         "description": "Python 脚本缺少模块级 docstring",
         "fix": "添加模块用途说明 docstring"},
    ],
    "javascript": [
        {"name": "hardcoded-versions", "severity": "warning",
         "description": "硬编码版本约束",
         "fix": "从 package.json 读取版本约束"},
        {"name": "missing-error-handling", "severity": "warning",
         "description": "异步操作缺少错误处理",
         "fix": "添加 try/catch 或 .catch()"},
    ],
    "shell": [
        {"name": "grep-pcre", "severity": "critical",
         "description": "grep -P 不可移植——BSD grep 不支持",
         "fix": "替换为 grep -E 或 perl"},
        {"name": "dev-null", "severity": "warning",
         "description": "使用了 /dev/null——在 Windows 上不可用",
         "fix": "使用跨平台写法"},
    ],
    "markdown": [
        {"name": "hardcoded-numbers", "severity": "info",
         "description": "文档中硬编码的数字计数",
         "fix": "用描述性文本替换数字"},
    ],
}


def match_anti_patterns(analysis: dict) -> list:
    """Match known anti-patterns based on skill structure analysis.

    Returns list of {pattern_name, severity, description, fix_strategy, file_glob}.
    """
    matched = []
    seen = set()

    for lang in analysis.get("languages", []):
        templates = PATTERN_TEMPLATES.get(lang, [])
        for t in templates:
            if t["name"] not in seen:
                matched.append({
                    "pattern_name": t["name"],
                    "severity": t["severity"],
                    "description": t["description"],
                    "fix_strategy": t["fix"],
                    "file_glob": f"*.{lang}" if lang != "markdown" else "*.md",
                    "source": "bootstrap-auto-matched"
                })
                seen.add(t["name"])

    # Always add cross-platform patterns
    cross_platform = [
        {"pattern_name": "language-drift", "severity": "warning",
         "description": "英文哨兵词检测——可能违反中文一致性",
         "file_glob": "scripts/*.py", "source": "bootstrap-default"},
    ]
    for cp in cross_platform:
        if cp["pattern_name"] not in seen:
            matched.append(cp)

    return matched


# ── Template generation ────────────────────────────────────────────────────────

def generate_patterns_md(skill_name: str, analysis: dict,
                          patterns: list) -> str:
    """Generate initial patterns.md content."""
    lines = [
        f"# 模式库 — {skill_name}",
        "",
        "> 由 skill-builder-v3 Bootstrap 自动生成 (v1.2)",
        f"> 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"> 检测语言: {', '.join(analysis.get('languages', ['unknown']))}",
        f"> 复杂度: {analysis.get('complexity', 'unknown')}",
        "",
        "---",
        "",
        "## 核心锚点",
        "",
        "| # | 锚点 | 验证方式 |",
        "|---|------|---------|",
        "| A | 通用性 | 同一流程对任意目标对等 —— 不给自身留特殊路径 |",
        "| B | 记录驱动 | 每个修复必须有事件日志 + known-issues 记录 |",
        "| C | 收敛 | 连续 N 轮零发现 = 停止 (注: 零发现 ≠ 零问题) |",
        "",
        "---",
        "",
        "## 自动匹配的反模式",
        "",
    ]

    for i, p in enumerate(patterns, 1):
        lines.extend([
            f"### 模式 {i}: {p['pattern_name']}",
            "",
            f"**严重级别:** {p.get('severity', 'warning')}",
            f"**来源:** {p.get('source', 'bootstrap')}",
            "",
            f"**症状:** {p.get('description', '?')}",
            "",
            f"**对策:** {p.get('fix_strategy', '待定义')}",
            "",
            "**检测方式:** (待 System 1 scanner 实现)",
            "",
            "---",
            "",
        ])

    lines.extend([
        "## 扫描器盲区",
        "",
        "以下问题类别当前扫描器无法检测 (诚实声明):",
        "- API 版本不兼容",
        "- 网络/超时/DNS/代理",
        "- 并发/竞态条件",
        "- 配置文件格式漂移",
        "- 依赖冲突",
        "- 权限问题",
        "",
        "> 盲区永远存在——诚实承认比假装全覆盖更有价值。",
    ])

    return "\n".join(lines)


def generate_known_issues_md(skill_name: str, analysis: dict) -> str:
    """Generate initial known-issues.md template."""
    ts = datetime.now(timezone.utc)
    return f"""# 已知问题与修复 — {skill_name}

> 格式：`<!-- @ttl version_added="X.Y" last_referenced_iso="YYYY-MM-DD" reference_count="0" -->`

<!-- @ttl version_added="1.0" last_referenced_iso="{ts.strftime('%Y-%m-%d')}" reference_count="0" -->
### [{ts.strftime('%Y-%m-%d')}] 免疫系统初始化
- **环境：** auto-detected
- **文件类型：** {', '.join(analysis.get('languages', ['unknown']))}
- **复杂度：** {analysis.get('complexity', 'unknown')} ({analysis.get('total_files', 0)} 文件)
- **现象：** 新 skill——免疫系统由 skill-builder-v3 Bootstrap 自动初始化
- **初始化内容：**
  - patterns.md ({len(match_anti_patterns(analysis))} 个初始模式)
  - known-issues.md (本文件)
  - manifest.json immune_system_status
  - (后续迭代将自动扩展检测规则)
- **验证：** `python engine/system1/scanner.py {skill_name}` 应正常返回
"""


# ── Main bootstrap ─────────────────────────────────────────────────────────────

def bootstrap_skill(skill_name: str, force: bool = False) -> dict:
    """Bootstrap a new skill's immune system.

    Only bootstraps if the skill lacks patterns.md / known-issues.md.
    Use force=True to overwrite existing files.
    """
    from engine import memory

    analysis = analyze_skill_structure(skill_name)
    if "error" in analysis:
        return analysis

    root = None
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            root = d
            break

    if not root:
        return {"error": f"技能 '{skill_name}' 不存在"}

    actions = []
    files_created = []

    # Create references/ directory
    refs_dir = root / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate patterns.md
    patterns_path = refs_dir / "patterns.md"
    if not patterns_path.exists() or force:
        patterns = match_anti_patterns(analysis)
        patterns_md = generate_patterns_md(skill_name, analysis, patterns)
        patterns_path.write_text(patterns_md, encoding="utf-8")
        files_created.append("references/patterns.md")
        actions.append(f"已创建 patterns.md ({len(patterns)} 个初始模式)")

    # 2. Generate known-issues.md
    ki_path = refs_dir / "known-issues.md"
    if not ki_path.exists() or force:
        ki_md = generate_known_issues_md(skill_name, analysis)
        ki_path.write_text(ki_md, encoding="utf-8")
        files_created.append("references/known-issues.md")
        actions.append("已创建 known-issues.md (免疫系统初始化记录)")

    # 3. Generate changelog-archive.json
    cl_path = refs_dir / "changelog-archive.json"
    if not cl_path.exists() or force:
        cl_path.write_text(json.dumps({
            "skill": skill_name,
            "entries": [{
                "version": "1.0.0",
                "iso": datetime.now(timezone.utc).isoformat(),
                "type": "bootstrap",
                "description": "免疫系统由 skill-builder-v3 Bootstrap 自动初始化",
                "source": "bootstrap.py"
            }]
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        files_created.append("references/changelog-archive.json")
        actions.append("已创建 changelog-archive.json")

    # 4. Update manifest.json with immune_system_status
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            mf = json.loads(manifest_path.read_text(encoding="utf-8"))
            mf["immune_system_status"] = {
                "constitution_exists": (root / "constitution.md").exists(),
                "patterns_exists": True,
                "known_issues_exists": True,
                "last_bootstrap_iso": datetime.now(timezone.utc).isoformat(),
                "bootstrap_source": "skill-builder-v3/P7",
                "bootstrap_patterns_count": len(match_anti_patterns(analysis)),
                "notes": "自动初始化——后续迭代将扩展检测规则"
            }
            manifest_path.write_text(json.dumps(mf, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
            actions.append("已更新 manifest.json immune_system_status")
        except (json.JSONDecodeError, OSError):
            pass  # TODO: log or re-raise

    # 5. Create data directories
    for subdir in ["logs", "insights", "snapshots", "metrics", "proposals",
                    "fix_quality", "global_insights", "generated_rules"]:
        (root / "data" / subdir).mkdir(parents=True, exist_ok=True)
    actions.append("已创建 data/ 目录结构")

    # Record event
    memory.write_event(skill_name, "scan", {
        "phase": "bootstrap",
        "analysis": analysis,
        "files_created": files_created,
        "patterns_count": len(match_anti_patterns(analysis))
    })

    # Write global insight for ALL skills (including self — no special path)
    memory.write_global_insight(
        f"Bootstrap 完成: {skill_name} ({analysis.get('complexity', '?')}, "
        f"{analysis.get('total_files', 0)} files, "
        f"{len(analysis.get('languages', []))} languages). "
        f"初始 {len(match_anti_patterns(analysis))} 个模式。"
        f"所有新 skill 可通过 `python engine/bootstrap.py run <skill>` 初始化。",
        [skill_name],
        pattern_name="bootstrap-success",
        confidence=0.8,
        source_skill=skill_name
    )

    return {
        "skill": skill_name,
        "analysis": analysis,
        "actions": actions,
        "files_created": files_created,
        "patterns_generated": len(match_anti_patterns(analysis)),
        "verdict": f"[OK] Bootstrap 完成——{len(actions)} 项操作, "
                   f"{len(files_created)} 个文件创建"
    }


# ── Dry-run preview ────────────────────────────────────────────────────────────

def preview_bootstrap(skill_name: str) -> dict:
    """Preview what bootstrap would do without writing files."""
    analysis = analyze_skill_structure(skill_name)
    if "error" in analysis:
        return analysis

    patterns = match_anti_patterns(analysis)
    root = None
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            root = d
            break

    existing = {
        "patterns.md": (root / "references" / "patterns.md").exists() if root else False,
        "known-issues.md": (root / "references" / "known-issues.md").exists() if root else False,
        "changelog-archive.json": (root / "references" / "changelog-archive.json").exists() if root else False,
    }

    return {
        "skill": skill_name,
        "analysis": analysis,
        "patterns_to_create": [p["pattern_name"] for p in patterns],
        "patterns_count": len(patterns),
        "would_overwrite": [k for k, v in existing.items() if v],
        "would_create": [k for k, v in existing.items() if not v],
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P7 Bootstrap (OpenSkill)——零起始Skill免疫系统初始化"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("analyze", help="分析 skill 结构")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("preview", help="预览 bootstrap 会做什么")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("run", help="执行 bootstrap")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--force", action="store_true", help="覆盖已有文件")

    args = parser.parse_args()

    if args.cmd == "analyze":
        analysis = analyze_skill_structure(args.skill)
        if "error" in analysis:
            print(f"[!!] {analysis['error']}")
            sys.exit(1)
        print(f"  Skill: {analysis['skill_name']}")
        print(f"  文件: {analysis['total_files']} | 语言: {analysis['languages']}")
        print(f"  复杂度: {analysis['complexity']}")
        print(f"  免疫系统: manifest={analysis['has_manifest']} "
              f"constitution={analysis['has_constitution']} "
              f"engine={analysis['has_engine']}")

    elif args.cmd == "preview":
        preview = preview_bootstrap(args.skill)
        if "error" in preview:
            print(f"[!!] {preview['error']}")
            sys.exit(1)
        print(f"  Bootstrap 预览: {args.skill}")
        print(f"  将创建 {preview['patterns_count']} 个初始模式:")
        for p in preview["patterns_to_create"]:
            print(f"    - {p}")
        if preview["would_overwrite"]:
            print(f"  ⚠️ 将覆盖: {preview['would_overwrite']}")

    elif args.cmd == "run":
        result = bootstrap_skill(args.skill, force=args.force)
        if "error" in result:
            print(f"[!!] {result['error']}")
            sys.exit(1)
        print(f"  {result['verdict']}")
        for a in result["actions"]:
            print(f"  {a}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
