#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P5: Hook Bridge — Claude Code Hook 集成适配器 (Claude Code 蒸馏).

v1.2 (2026-06-13): 蒸馏自 Claude Code Hook 系统 + Compound Agent 7-hook模式.

Claude Code 的 Hook 系统是自进化从"手动工具"到"自治Agent"的关键跨越。
Hook Bridge 提供 CLI 命令供 hooks 调用，实现自动触发而非等待用户命令。

四个核心 Hook 命令:
  session-start  → 预加载 insights, 注射已知问题, GVU 健康检查
  post-failure   → 捕捉工具失败, 自动诊断, 写入事件
  session-stop   → 自动 loop 收敛检查, 记录结果, 内存索引同步
  gvu-guard      → 实时 GVU SNR 监控, 不稳定时暂停自动修复

用法示例 (在 .claude/settings.json 中):
  { "hooks": { "SessionStart": [
      {"command": "python engine/hooks_bridge.py session-start --target ${CLAUDE_SKILL}"}
  ]}}
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Config ─────────────────────────────────────────────────────────────────────

def _data_dir(skill_name: str) -> Path:
    from engine import memory
    root = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in root.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            return d / "data"
    return SKILL_ROOT / "data"


# ── Hook: session-start ────────────────────────────────────────────────────────

def hook_session_start(skill_name: str, verbose: bool = True) -> dict:
    """SessionStart hook: pre-load everything the agent needs.

    Injects into context:
      1. Recent insights (last 5)
      2. Known issues still active
      3. GVU stability status
      4. Bootstrap memory index if empty
    """
    from engine import memory, memory_index

    report = {"skill": skill_name, "hook": "SessionStart", "actions": []}

    # 1. Load recent insights
    insights = memory.read_insights(skill_name, limit=5)
    if insights:
        report["insights"] = [
            {"id": ins.get("insight_id", "?")[-20:],
             "text": ins.get("text", "")[:200],
             "confidence": ins.get("confidence", 0)}
            for ins in insights
        ]
        report["actions"].append(f"已加载 {len(insights)} 条洞察")

    # 2. Current known issues (active patterns)
    scan_result = subprocess.run(
        [sys.executable, str(SKILL_ROOT / "engine" / "system1" / "scanner.py"),
         skill_name, "--json"],
        capture_output=True, text=True, timeout=30,
        cwd=str(SKILL_ROOT), encoding="utf-8", errors="replace"
    )
    if scan_result.returncode == 0:
        try:
            findings = json.loads(scan_result.stdout).get("findings_count", 0)
            report["active_findings"] = findings
            report["actions"].append(f"当前发现: {findings} 个问题")
        except json.JSONDecodeError:
            pass  # TODO: log or re-raise

    # 3. GVU health check
    gvu = memory.calculate_gvu_snr(skill_name)
    report["gvu"] = {"stable": gvu["stable"], "verdict": gvu["verdict"]}
    if not gvu["stable"]:
        report["actions"].append(f"⚠️ GVU 警告: {gvu['verdict']}")

    # 4. Memory index bootstrap
    stats = memory_index.memory_index_stats(skill_name)
    if stats["total_docs"] == 0:
        boot = memory_index.bootstrap_if_empty(skill_name)
        report["actions"].append(f"记忆索引初始化: {boot.get('total', 0)} 条")
    else:
        report["memory_index"] = f"{stats['total_docs']} docs"

    if verbose:
        print(f"[Hook:SessionStart] {skill_name}")
        for a in report["actions"]:
            print(f"  {a}")

    memory.write_event(skill_name, "loop", {
        "phase": "session_start_hook",
        "insights_loaded": len(insights),
        "active_findings": report.get("active_findings", 0),
        "gvu_stable": gvu["stable"]
    })

    return report


# ── Hook: post-tool-failure ────────────────────────────────────────────────────

def hook_post_failure(skill_name: str, tool_name: str = "",
                       error_message: str = "", verbose: bool = True) -> dict:
    """PostToolUseFailure hook: auto-diagnose tool failures.

    When a tool fails (e.g., grep, python, git), this hook:
      1. Records the failure as an event
      2. Checks if this is a known failure pattern
      3. If recurring (3+ times in recent events), flags for System 2
    """
    from engine import memory, memory_index

    report = {"skill": skill_name, "hook": "PostToolUseFailure",
              "tool": tool_name, "actions": []}

    # Record failure
    memory.write_event(skill_name, "fix", {
        "phase": "tool_failure_hook",
        "tool": tool_name,
        "error": error_message[:500],
        "iso": datetime.now(timezone.utc).isoformat()
    })

    # Index for later retrieval
    memory_index.index_document(
        doc_type="event",
        skill_name=skill_name,
        title=f"工具失败: {tool_name}",
        content=error_message[:1000],
        confidence=0.3,
        metadata={"tool": tool_name, "source": "post_failure_hook"}
    )

    report["actions"].append(f"已记录工具失败: {tool_name}")

    # Check recurrence
    events = memory.read_events(skill_name, limit=20)
    recent_failures = [
        e for e in events
        if e.get("data", {}).get("phase") == "tool_failure_hook"
        and e.get("data", {}).get("tool") == tool_name
    ]
    if len(recent_failures) >= 3:
        memory.write_insight(
            skill_name,
            f"重复工具失败: {tool_name} 在最近 {len(recent_failures)} 次中失败。"
            f"错误: {error_message[:200]}。建议 System 2 介入分析。",
            events[:5], confidence=0.7
        )
        report["actions"].append(f"⚠️ 重复失败 ({len(recent_failures)}次) — System 2 已通知")

    if verbose:
        print(f"[Hook:PostFailure] {tool_name}: {error_message[:120]}")

    return report


# ── Hook: session-stop ─────────────────────────────────────────────────────────

def hook_session_stop(skill_name: str, verbose: bool = True) -> dict:
    """Stop hook: auto-loop check + convergence report + memory sync.

    Fires when the session is about to end. Runs:
      1. Fast loop dry-run to check convergence
      2. Memory index sync (index any new events)
      3. GVU stability snapshot
      4. TAPO rule quality check (any rules need demotion?)
    """
    from engine import memory, memory_index

    report = {"skill": skill_name, "hook": "Stop", "actions": []}

    # 1. Fast convergence check
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "engine" / "loop.py"),
             "--target", skill_name, "--dry-run"],
            capture_output=True, text=True, timeout=30,
            cwd=str(SKILL_ROOT), encoding="utf-8", errors="replace"
        )
        report["dry_run"] = {"exit": result.returncode,
                             "summary": result.stdout[-300:]}
        report["actions"].append(f"收敛检查完成 (exit={result.returncode})")
    except Exception as e:
        report["dry_run_error"] = str(e)

    # 2. Memory index sync
    new_count = memory_index.auto_index_events(skill_name, limit=50)
    if new_count > 0:
        report["actions"].append(f"记忆索引同步: {new_count} 条新事件")

    # 3. GVU snapshot
    gvu = memory.calculate_gvu_snr(skill_name)
    memory.write_metric(skill_name, "gvu_snapshot_snr", gvu["verifier_snr"],
                        {"hook": "session_stop"})
    report["gvu"] = gvu["verdict"]

    # 4. TAPO rule quality
    from engine.system1 import rules
    demotions = rules.check_and_maybe_demote_rules(skill_name)
    if demotions:
        report["actions"].append(f"TAPO 规则降级: {list(demotions.keys())}")

    # 5. P6: Metaproductivity check (record session-level descendant quality)
    try:
        from engine import memory as mem
        mem.update_descendant_quality_aggregate(skill_name)
        report["actions"].append("元生产力聚合已更新")
    except (ImportError, AttributeError):
        pass  # TODO: log or re-raise

    if verbose:
        print(f"[Hook:Stop] {skill_name}")
        for a in report["actions"]:
            print(f"  {a}")

    memory.write_event(skill_name, "loop", {
        "phase": "session_stop_hook",
        "gvu_stable": gvu["stable"]
    })

    return report


# ── Hook: gvu-guard (for PreToolUse) ───────────────────────────────────────────

def hook_gvu_guard(skill_name: str, tool_name: str = "",
                    verbose: bool = True) -> dict:
    """PreToolUse hook: GVU guard — block destructive tools if system is unstable.

    Before any file-modifying tool runs, check GVU stability.
    If unstable, log warning and optionally block (in trusted mode).
    """
    from engine import memory

    gvu = memory.calculate_gvu_snr(skill_name)
    blocked = not gvu["stable"]

    if blocked:
        memory.write_event(skill_name, "loop", {
            "phase": "gvu_guard_blocked",
            "tool": tool_name,
            "gvu_verdict": gvu["verdict"]
        })

    if verbose and blocked:
        print(f"[Hook:GVU-Guard] ⚠️ 不稳定——{tool_name} 调用被门控阻止: {gvu['verdict']}")

    return {"blocked": blocked, "gvu": gvu}


# ── Hook config generator ──────────────────────────────────────────────────────

SETTINGS_JSON_HOOKS = {
    "description": "自进化 Hook 配置 (skill-builder v1.2) — 将 <target-skill> 替换为实际skill名",
    "hooks": {
        "SessionStart": [
            {
                "matcher": "",
                "command": f'"{sys.executable}" "{SKILL_ROOT / "engine" / "hooks_bridge.py"}" session-start --target <target-skill>'
            }
        ],
        "PostToolUseFailure": [
            {
                "matcher": "",
                "command": f'"{sys.executable}" "{SKILL_ROOT / "engine" / "hooks_bridge.py"}" post-failure --target <target-skill> --tool "$CLAUDE_TOOL_NAME" --error "$CLAUDE_ERROR"'
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "command": f'"{sys.executable}" "{SKILL_ROOT / "engine" / "hooks_bridge.py"}" session-stop --target <target-skill>'
            }
        ]
    }
}


def generate_hook_config(skill_name: str) -> str:
    """Generate hook configuration JSON for pasting into .claude/settings.json."""
    config = {
        "description": f"skill-builder 自进化 Hook 配置 — {skill_name}",
        "hooks": {
            "SessionStart": [
                {"matcher": "",
                 "command": f'python engine/hooks_bridge.py session-start --target {skill_name}'}
            ],
            "PostToolUseFailure": [
                {"matcher": "",
                 "command": f'python engine/hooks_bridge.py post-failure --target {skill_name}'}
            ],
            "Stop": [
                {"matcher": "",
                 "command": f'python engine/hooks_bridge.py session-stop --target {skill_name}'}
            ]
        }
    }
    return json.dumps(config, indent=2, ensure_ascii=False)


# ── (v1.3) Auto-install hooks into settings.json ────────────────────────────────

def install_hooks(skill_name: str, force: bool = False) -> dict:
    """Auto-install self-evolution hooks into .claude/settings.json.

    This is the key P5 closure — from "manual tool" to "autonomous agent."
    Reads current settings.json, merges in the hook configuration, and writes back.

    Args:
        skill_name: 目标 skill 名称 (默认: skill-builder-v3 自身)
        force: 即使已有 hooks 也强制覆盖

    Returns {installed, hooks_added, hooks_skipped, backup_path, warnings}.
    """
    settings_path = Path(os.environ.get("SB3_CONFIG_DIR", str(Path.home() / ".claude"))) / "settings.json"
    result = {
        "installed": False,
        "hooks_added": [],
        "hooks_skipped": [],
        "backup_path": None,
        "warnings": [],
        "skill": skill_name
    }

    if not settings_path.exists():
        result["warnings"].append("settings.json 不存在——无法安装 hooks")
        return result

    # Read current settings
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        result["warnings"].append(f"settings.json 读取失败: {e}")
        return result

    # Backup before modifying
    backup_path = settings_path.with_suffix(".json.bak")
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    # Copy to backup
    import shutil
    shutil.copy2(str(settings_path), str(backup_path))
    result["backup_path"] = str(backup_path)

    # Build hook commands
    skill_root_abs = SKILL_ROOT
    python_exe = sys.executable
    bridge_script = str(skill_root_abs / "engine" / "hooks_bridge.py")

    new_hooks = {
        "SessionStart": [{
            "matcher": "",
            "command": f'"{python_exe}" "{bridge_script}" session-start --target {skill_name}'
        }],
        "PostToolUseFailure": [{
            "matcher": "",
            "command": f'"{python_exe}" "{bridge_script}" post-failure --target {skill_name} --tool "$CLAUDE_TOOL_NAME" --error "$CLAUDE_ERROR"'
        }],
        "Stop": [{
            "matcher": "",
            "command": f'"{python_exe}" "{bridge_script}" session-stop --target {skill_name}'
        }],
        "PreToolUse": [{
            "matcher": "Bash",
            "command": f'"{python_exe}" "{bridge_script}" gvu-guard --target {skill_name} --tool "$CLAUDE_TOOL_NAME"'
        }]
    }

    # Merge into existing hooks
    existing_hooks = settings.get("hooks", {})
    if not existing_hooks:
        existing_hooks = {}

    for hook_name, hook_entries in new_hooks.items():
        if hook_name in existing_hooks and not force:
            result["hooks_skipped"].append(hook_name)
            continue
        existing_hooks[hook_name] = hook_entries
        result["hooks_added"].append(hook_name)

    if not result["hooks_added"]:
        result["warnings"].append("所有 hooks 已存在——使用 --force 强制覆盖")
        return result

    # Write back
    settings["hooks"] = existing_hooks
    # Add note about hooks
    if "_hook_note" not in settings:
        settings["_hook_note"] = (
            f"自进化 hooks 由 skill-builder-v3 v1.3 自动安装 ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})。"
            f"目标 skill: {skill_name}。"
            f"如需移除，删除 hooks 键并移除 _hook_note。"
        )

    try:
        settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        result["installed"] = True
        print(f"[Hook:Install] ✅ {len(result['hooks_added'])} hooks 已安装到 settings.json")
        for h in result["hooks_added"]:
            print(f"  → {h}")
        if result["hooks_skipped"]:
            print(f"  ⏭ 跳过: {result['hooks_skipped']} (已存在)")
    except OSError as e:
        result["warnings"].append(f"写入 settings.json 失败: {e}")

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P5 Hook Bridge — Claude Code Hook 集成适配器"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("session-start", help="SessionStart hook")
    p.add_argument("--target", required=True, help="目标 skill")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("post-failure", help="PostToolUseFailure hook")
    p.add_argument("--target", required=True)
    p.add_argument("--tool", default="", help="失败的工具名")
    p.add_argument("--error", default="", help="错误信息")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("session-stop", help="Stop hook")
    p.add_argument("--target", required=True)
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("gvu-guard", help="PreToolUse GVU stability guard")
    p.add_argument("--target", required=True)
    p.add_argument("--tool", default="")

    p = sub.add_parser("generate-config", help="生成 Hook 配置 JSON")
    p.add_argument("--target", required=True)

    p = sub.add_parser("install", help="自动安装 hooks 到 settings.json")
    p.add_argument("--target", required=True, help="目标 skill 名称")
    p.add_argument("--force", action="store_true", help="强制覆盖已有 hooks")

    args = parser.parse_args()

    if args.cmd == "session-start":
        hook_session_start(args.target, verbose=not args.quiet)
    elif args.cmd == "post-failure":
        hook_post_failure(args.target, args.tool, args.error,
                          verbose=not args.quiet)
    elif args.cmd == "session-stop":
        hook_session_stop(args.target, verbose=not args.quiet)
    elif args.cmd == "gvu-guard":
        result = hook_gvu_guard(args.target, args.tool)
        if result["blocked"]:
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(1)
    elif args.cmd == "generate-config":
        print(generate_hook_config(args.target))
    elif args.cmd == "install":
        result = install_hooks(args.target, force=args.force)
        if result["installed"]:
            print(f"\n  ✅ Hooks 安装成功! 下次会话将自动触发自进化循环。")
            print(f"  备份: {result.get('backup_path', '?')}")
        else:
            print(f"\n  ⚠️ 安装未完成: {result.get('warnings', [])}")
            if result.get("hooks_skipped"):
                print(f"  使用 --force 强制覆盖")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
