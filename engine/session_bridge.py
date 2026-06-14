#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Session Bridge — Claude Code 自主进化引擎。

C 路线实现。Claude Code 本身就是 LLM——不需要 API Key，不需要人盯着。
每次 session 开始和结束时自动触发。Session 之间通过持久文件传递状态。

架构:
  Session N 结束 → 写入 data/session_state.json（下个 session 的起点）
  Session N+1 开始 → Hook 触发 → 生成 decision_brief.md →
  Claude 读到此 brief → 自主执行进化决策树 →
  Session N+1 结束 → 更新 session_state.json

这个模块只需要两个 hook:
  SessionStart: 生成 decision brief，注入 Claude 上下文
  Stop: 保存本轮状态，供下个 session 继承

Usage:
  python engine/session_bridge.py start <skill-name>
    → 输出完整的 decision brief (markdown)，供 Claude 阅读

  python engine/session_bridge.py stop <skill-name>
    → 保存本轮 session 摘要到 data/session_state.json

  python engine/session_bridge.py install-hooks
    → 自动安装 SessionStart/Stop hooks 到 settings.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.platform import find_skill_root as _find_skill_root


# ── State persistence ──────────────────────────────────────────────────────────

def _state_path(skill_name: str) -> Optional[Path]:
    root = _find_skill_root(skill_name)
    if not root:
        return None
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session_state.json"


def read_session_state(skill_name: str) -> dict:
    """Read the state left by the previous session."""
    sp = _state_path(skill_name)
    if not sp or not sp.exists():
        return {"sessions_completed": 0, "first_session": True}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sessions_completed": 0, "first_session": True, "corrupted": True}


def write_session_state(skill_name: str, state: dict):
    """Write session state for the next session to inherit."""
    sp = _state_path(skill_name)
    if not sp:
        return
    sp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Decision brief: what Claude reads at session start ────────────────────────

def generate_decision_brief(skill_name: str) -> str:
    """Generate the complete decision brief for Claude at session start.

    This is the C route's core: a self-contained markdown document that tells
    Claude everything it needs to know to autonomously run the evolution cycle.
    Claude reads this document, executes the decision tree, and records results.

    Returns a markdown string ready for injection into Claude's context.
    """
    from engine import memory, verdict
    from engine.system1 import scanner, rules as rules_mod
    from engine.system2 import rule_generator

    prev_state = read_session_state(skill_name)
    scan = scanner.scan_skill(skill_name)
    gvu = memory.calculate_gvu_snr(skill_name)
    active = rules_mod.get_active_rules(skill_name)
    quals = memory.get_all_fix_qualities(skill_name)
    v_stats = verdict.verdict_stats(skill_name)
    insights = memory.read_insights(skill_name, limit=5)

    # Determine what needs attention
    pseudo_patterns = _find_persistent_pseudo_fixes(skill_name)
    skipped_patterns = _find_persistent_skips(skill_name)

    # ── Build the brief ──
    brief = f"""# Evolution Decision Brief — {skill_name}

> Session #{prev_state.get('sessions_completed', 0) + 1}
> Generated: {datetime.now(timezone.utc).isoformat()[:19]}
> Previous session: {prev_state.get('last_session_iso', 'never')[:19]}

## STATE SUMMARY

**Scanner**: {scan.get('findings_count', 0)} findings
**GVU Stability**: {gvu.get('verdict', '?')}
**Active Rules**: {len(active)} total ({len([r for r in active if r.get('auto_apply')])} auto-apply)
**Fix Quality Issues**: {len([1 for q in quals.values() if q.get('success_rate', 1.0) < 0.5 and q.get('total', 0) >= 5])}
**Claude Verdicts**: {v_stats.get('total', 0)} total ({v_stats.get('genuine_fix_rate', 0):.0%} genuine, {v_stats.get('pseudo_fix_rate', 0):.0%} pseudo)
**Health**: {v_stats.get('health', '?')}

## CURRENT FINDINGS

"""
    for f in scan.get("findings", []):
        pattern = f.get("pattern", "?")
        matching = [r for r in active if r.get("pattern") == pattern]
        rule_name = matching[0]["name"] if matching else "NO_RULE"
        has_fix = "fix" in matching[0] if matching else False
        auto = matching[0].get("auto_apply", False) if matching else False
        qual = quals.get(rule_name, {})
        qual_str = f"quality={qual.get('success_rate', 1.0):.0%} ({qual.get('total', 0)} fixes)" if qual.get('total', 0) > 0 else "no fix history"
        brief += f"- [{f.get('severity', '?')}] **{pattern}**: {f.get('file', '?')}:{f.get('line', '?')}\n"
        brief += f"  Rule: {rule_name} | has_fix={has_fix} | auto={auto} | {qual_str}\n"
        brief += f"  {f.get('description', '')[:200]}\n"

    if not scan.get("findings", []):
        brief += "  (no findings)\n"

    brief += f"""
## PERSISTENT PSEUDO FIXES (must be rewritten this session)

"""
    if pseudo_patterns:
        for pp in pseudo_patterns:
            brief += f"- **{pp['pattern']}**: {pp['count']} consecutive PSEUDO_FIX verdicts. Rule: {pp['rule']}\n"
            brief += f"  Last explanation: {pp.get('explanation', '')[:200]}\n"
            brief += f"  **ACTION REQUIRED**: Read fix function source → rewrite to genuinely change behavior.\n"
            brief += f"  File: engine/system1/rules.py (seed rule) or data/generated_rules/<id>.py (generated rule)\n"
    else:
        brief += "  (none — all recent verdicts are GENUINE or this is the first session)\n"

    brief += f"""
## PERSISTENT SKIPS (scanner or fix needs adjustment)

"""
    if skipped_patterns:
        for sp in skipped_patterns:
            brief += f"- **{sp['pattern']}**: {sp['count']} consecutive SKIP verdicts. Reason: {sp.get('reason', '?')[:200]}\n"
            brief += f"  **ACTION REQUIRED**: If scanner false positive → adjust regex or exclude_files in scanner.py.\n"
            brief += f"  If genuinely not worth fixing → document in known-issues.md.\n"
    else:
        brief += "  (none)\n"

    brief += f"""
## RECENT INSIGHTS

"""
    for ins in insights[:3]:
        brief += f"- [{ins.get('confidence', 0):.0%}] {ins.get('text', '')[:300]}\n"

    if not insights:
        brief += "  (no insights yet)\n"

    brief += f"""
## DEEP AUDIT STATUS

"""
    try:
        from engine import deep_audit
        check = deep_audit.should_run_deep_audit(skill_name)
        brief += f"- {check.get('reason', '?')}\n"
        if check.get('should_run'):
            brief += f"  **ACTION**: Run `python engine/deep_audit.py {skill_name} guided` to find semantic defects.\n"
    except ImportError:
        brief += "  (deep_audit module not available)\n"

    brief += f"""
## YOUR TASK THIS SESSION

You are the evolution engine. Read the state above. Execute the v4.2 decision tree:

1. **Judge each finding.** For each finding above, determine:
   - GENUINE_FIX: the fix function genuinely changes behavior → apply it
   - PSEUDO_FIX: the fix function only adds comments → **rewrite the fix function first** (Step 2.5)
   - SKIP: genuinely not worth fixing → adjust scanner or document in known-issues

2. **Persistent pseudo fixes first.** If there are patterns listed under PERSISTENT PSEUDO FIXES:
   - Read the fix function source code
   - Rewrite it to genuinely change behavior (not just add # TODO comments)
   - This is MANDATORY. Do not skip.

3. **Deep audit if needed.** If DEEP AUDIT STATUS says should_run → run guided audit.

4. **Apply genuine fixes.** After rewriting any pseudo fix functions:
   ```bash
   python engine/loop.py apply --target {skill_name} --fixes '<json>'
   ```

5. **Verify independently.** For each applied fix:
   ```bash
   python engine/loop.py verify --target {skill_name} --json
   ```
   Judge for yourself: did this genuinely improve the code?

6. **Record verdicts.** For each fix:
   ```bash
   python engine/verdict.py {skill_name} record --rule <name> --file <path> --verdict <GENUINE_FIX|PSEUDO_FIX|REGRESSION|SKIP> --explanation "..."
   ```

7. **Reflect.** Check stats:
   ```bash
   python engine/verdict.py {skill_name} stats
   ```
   If pseudo_fix_rate > 30% → something is wrong. Fix the fix functions.

## SESSION REFERENCE

- Start with: `python engine/loop.py context --target {skill_name} --json` for full technical state
- Command quick list: scan | apply | verify | record | stats | guided | prescan
- Previous session verdict count: {v_stats.get('total', 0)}
"""

    return brief


# ── Persistent pattern tracking ────────────────────────────────────────────────

def _find_persistent_pseudo_fixes(skill_name: str, lookback: int = 20) -> list:
    """Find patterns that have consecutive PSEUDO_FIX verdicts in recent history."""
    from engine import verdict
    all_v = verdict.read_recent_verdicts(skill_name, limit=lookback)
    all_v.reverse()  # chronological order

    pattern_counts = {}
    for v in all_v:
        if v.get('verdict') == 'PSEUDO_FIX':
            rule = v.get('fix_rule', '?')
            pattern = v.get('finding_pattern', rule)
            if pattern not in pattern_counts:
                pattern_counts[pattern] = {'count': 0, 'rule': rule, 'explanation': ''}
            pattern_counts[pattern]['count'] += 1
            pattern_counts[pattern]['explanation'] = v.get('explanation', '')

    return [{'pattern': p, **d} for p, d in pattern_counts.items() if d['count'] >= 2]


def _find_persistent_skips(skill_name: str, lookback: int = 20) -> list:
    """Find patterns with consecutive SKIP verdicts."""
    from engine import verdict
    all_v = verdict.read_recent_verdicts(skill_name, limit=lookback)
    all_v.reverse()

    pattern_counts = {}
    for v in all_v:
        if v.get('verdict') == 'SKIP':
            rule = v.get('fix_rule', '?')
            if rule not in pattern_counts:
                pattern_counts[rule] = {'count': 0, 'reason': ''}
            pattern_counts[rule]['count'] += 1
            pattern_counts[rule]['reason'] = v.get('explanation', '')

    return [{'pattern': p, **d} for p, d in pattern_counts.items() if d['count'] >= 2]


# ── Hook entry points ──────────────────────────────────────────────────────────

def hook_session_start(skill_name: str = "skill-builder-v3") -> dict:
    """SessionStart hook: generate decision brief and output it.

    Called by Claude Code hook. Outputs markdown that injects into
    Claude's context, telling it exactly what to do this session.

    Usage in settings.json:
      "SessionStart": [{ "command": "python engine/session_bridge.py start skill-builder-v3" }]
    """
    brief = generate_decision_brief(skill_name)
    print(brief)
    return {"brief_length": len(brief), "skill": skill_name}


def hook_session_stop(skill_name: str = "skill-builder-v3") -> dict:
    """Stop hook: save session summary for next session.

    Called when Claude Code session ends. Records:
    - What happened this session (fixes applied, verdicts recorded)
    - State for next session to inherit

    Usage in settings.json:
      "Stop": [{ "command": "python engine/session_bridge.py stop skill-builder-v3" }]
    """
    from engine import memory, verdict

    state = read_session_state(skill_name)
    v_stats = verdict.verdict_stats(skill_name)
    scan = __import__("engine.system1.scanner", fromlist=["scan_skill"]).scan_skill(skill_name)

    state["sessions_completed"] = state.get("sessions_completed", 0) + 1
    state["last_session_iso"] = datetime.now(timezone.utc).isoformat()
    state["last_session_findings"] = scan.get("findings_count", 0)
    state["last_session_verdicts"] = v_stats.get("total", 0)
    state["last_session_genuine_rate"] = v_stats.get("genuine_fix_rate", 0)
    state["last_session_pseudo_rate"] = v_stats.get("pseudo_fix_rate", 0)
    state["first_session"] = False

    # Persist findings for comparison next session
    state["last_findings_patterns"] = [
        f.get("pattern", "?") for f in scan.get("findings", [])
    ]

    write_session_state(skill_name, state)

    print(f"\n[Session Bridge] Saved state for session #{state['sessions_completed']}")
    print(f"  Findings: {state['last_session_findings']}")
    print(f"  Verdicts: {state['last_session_verdicts']} ({state['last_session_genuine_rate']:.0%} genuine)")
    print(f"  Next session: python engine/session_bridge.py start {skill_name}")

    return state


# ── Hook installer ─────────────────────────────────────────────────────────────

def install_session_hooks(skill_name: str = "skill-builder-v3", force: bool = False):
    """Install SessionStart and Stop hooks into settings.json.

    After this, every Claude Code session for this skill will:
    - Start with: decision brief in context
    - End with: state saved for next session

    The C route is now active. No human intervention needed beyond the initial install.
    """
    import os, shutil

    settings_path = Path(os.environ.get(
        "SB3_CONFIG_DIR",
        str(Path.home() / ".claude")
    )) / "settings.json"

    if not settings_path.exists():
        print(f"[!!] settings.json not found at {settings_path}")
        print(f"     Create it first, or run: mkdir -p {settings_path.parent}")
        return False

    # Backup
    backup = settings_path.with_suffix(".json.bak")
    shutil.copy2(str(settings_path), str(backup))

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        settings = {}

    python_exe = sys.executable
    bridge_script = str(SKILL_ROOT / "engine" / "session_bridge.py")

    # The hooks to install
    new_hooks = {
        "SessionStart": [{
            "matcher": "",
            "command": f'"{python_exe}" "{bridge_script}" start {skill_name}'
        }],
        "Stop": [{
            "matcher": "",
            "command": f'"{python_exe}" "{bridge_script}" stop {skill_name}'
        }]
    }

    existing = settings.get("hooks", {})
    if not force and any(h in existing for h in new_hooks):
        print("[--] Hooks already exist. Use --force to overwrite.")
        return False

    existing.update(new_hooks)
    settings["hooks"] = existing

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"[OK] C-route hooks installed to {settings_path}")
    print(f"  SessionStart: decision brief injected into Claude context")
    print(f"  Stop:         session state saved for next time")
    print(f"  Backup:       {backup}")
    print(f"  Skill:        {skill_name}")
    return True


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Session Bridge — Claude Code 自主进化引擎 (C 路线)"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("start", help="[SessionStart hook] 生成决策简报")
    p.add_argument("skill", help="目标 skill 名称")

    p = sub.add_parser("stop", help="[Stop hook] 保存 session 状态")
    p.add_argument("skill", help="目标 skill 名称")

    p = sub.add_parser("install-hooks", help="安装 SessionStart/Stop hooks 到 settings.json")
    p.add_argument("--skill", default="skill-builder-v3", help="目标 skill 名称")
    p.add_argument("--force", action="store_true", help="强制覆盖已有 hooks")

    p = sub.add_parser("state", help="查看上次 session 保存的状态")
    p.add_argument("skill", help="目标 skill 名称")

    args = parser.parse_args()

    if args.cmd == "start":
        hook_session_start(args.skill)

    elif args.cmd == "stop":
        hook_session_stop(args.skill)

    elif args.cmd == "install-hooks":
        install_session_hooks(args.skill, force=args.force)

    elif args.cmd == "state":
        state = read_session_state(args.skill)
        if state.get("first_session"):
            print(f"[--] No previous session state for {args.skill}")
            print(f"     Run: python engine/session_bridge.py start {args.skill}")
        else:
            print(f"Session #{state.get('sessions_completed', '?')}")
            print(f"Last: {state.get('last_session_iso', '?')[:19]}")
            print(f"Findings: {state.get('last_session_findings', '?')}")
            print(f"Verdicts: {state.get('last_session_verdicts', '?')}")
            print(f"Genuine rate: {state.get('last_session_genuine_rate', 0):.0%}")

    else:
        parser.print_help()
        print(f"\n  C 路线架构:")
        print(f"    Session N ends   → python engine/session_bridge.py stop <skill>")
        print(f"    Session N+1 starts → python engine/session_bridge.py start <skill>")
        print(f"    Install:            python engine/session_bridge.py install-hooks")
        print(f"    After install, every Claude Code session auto-evolves.")


if __name__ == "__main__":
    main()
