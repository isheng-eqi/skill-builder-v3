#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Context Builder — 为 Claude 构建完整决策上下文。

v4 架构翻转的核心模块。原来的 loop.py 是 Python 自动运行进化，
Claude 只能看到 print() 摘要。现在 Claude 是决策者——本模块
为 Claude 提供做判断所需的全部结构化数据。

Usage:
  python engine/context.py <skill-name>           → 完整上下文 JSON 到 stdout
  python engine/context.py <skill-name> --compact  → 紧凑版（跳过源码）
  python engine/context.py <skill-name> --scan-only → 仅扫描 findings

Claude 读取此 JSON 后，在每个发现上做独立判断：
  - 这个 finding 值得修吗？
  - 对应的 fix 函数真的改变行为吗？
  - 上次这个规则修完后真的改善了吗？
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from engine import memory
from engine.platform import find_skill_root as _find_skill_root


def build_context(skill_name: str, compact: bool = False) -> dict:
    """Build the complete decision context for Claude.

    Returns structured JSON with all information Claude needs
    to make intelligent evolution decisions.
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在"}

    ctx = {
        "meta": {
            "skill": skill_name,
            "iso_built": datetime.now(timezone.utc).isoformat(),
            "version": "v4-claude-driven",
            "instruction": (
                "Claude: 阅读此上下文后，对每个 finding 做独立判断。"
                "不要信任 Python 的 auto_apply——检查 fix 函数源码，判断它是否真正改变了行为。"
                "标注每个 finding: GENUINE_FIX / PSEUDO_FIX / NEEDS_HUMAN / SKIP。"
                "然后用 engine/verdict.py 写入你的判断。"
            )
        }
    }

    # ── 1. Current findings (complete, not summarized) ──
    from engine.system1 import scanner
    scan_result = scanner.scan_skill(skill_name)
    ctx["findings"] = {
        "total": scan_result.get("findings_count", 0),
        "by_severity": scan_result.get("by_severity", {}),
        "items": scan_result.get("findings", [])
    }

    # ── 2. Active rules with fix function source ──
    from engine.system1 import rules as rules_mod
    active_rules = rules_mod.get_active_rules(skill_name)
    ctx["rules"] = _describe_rules(active_rules, compact)

    # ── 3. Fix quality per rule (TAPO data) ──
    ctx["fix_qualities"] = memory.get_all_fix_qualities(skill_name)

    # ── 4. Recent events ──
    recent_events = memory.read_events(skill_name, limit=30)
    ctx["recent_events"] = _summarize_events(recent_events)

    # ── 5. Execution traces with actual output ──
    exec_traces = [e for e in recent_events if e["event_type"] == "execution_trace"]
    ctx["execution_traces"] = [
        {
            "iso": e.get("iso_timestamp", "?")[:19],
            "phase": e.get("data", {}).get("phase", "?"),
            "success": e.get("data", {}).get("success", "?"),
            "verdict": e.get("data", {}).get("verdict", "?"),
            "command": e.get("data", {}).get("command", "?")[:200],
            "stdout": e.get("data", {}).get("stdout_tail", "")[:300],
            "stderr": e.get("data", {}).get("stderr_tail", "")[:300],
            "fix_rule": e.get("data", {}).get("fix_rule", "?"),
            "fix_file": e.get("data", {}).get("fix_file", "?"),
        }
        for e in exec_traces[-15:]
    ]

    # ── 6. Recent insights ──
    insights = memory.read_insights(skill_name, limit=10)
    ctx["insights"] = [
        {
            "id": ins.get("insight_id", "?")[-30:],
            "text": ins.get("text", "")[:500],
            "confidence": ins.get("confidence", 0)
        }
        for ins in insights
    ]

    # ── 7. GVU stability ──
    gvu = memory.calculate_gvu_snr(skill_name)
    ctx["stability"] = {
        "gvu": gvu,
        "fitness": _calculate_fitness_snapshot(skill_name)
    }

    # ── 8. Generated rules (probation + active) ──
    try:
        from engine.system2 import rule_generator
        gen_rules = rule_generator.load_all_generated_rules(skill_name)
        ctx["generated_rules"] = [
            {
                "rule_id": r.get("rule_id", "?")[-30:],
                "name": r.get("name", "?"),
                "pattern": r.get("pattern", "?"),
                "status": r.get("status", "?"),
                "auto_apply": r.get("auto_apply", False),
                "fix_strategy": r.get("fix_strategy", "")[:300],
                "has_code": bool(r.get("_generated_code_path")),
                "probation": r.get("probation", {}).get("remaining", "?"),
                "success_rate": (
                    r.get("probation", {}).get("successes", 0) /
                    max(1, r.get("probation", {}).get("successes", 0) +
                        r.get("probation", {}).get("failures", 0))
                ) if r.get("probation", {}) else None
            }
            for r in gen_rules if r.get("status") != "deprecated"
        ]
    except ImportError:
        ctx["generated_rules"] = []

    # ── 9. Blindspot status ──
    try:
        from engine import blindspot
        unruly = blindspot.collect_unruly_patterns(skill_name, lookback=5)
        ctx["blindspots"] = {
            "candidates": list(unruly.get("blindspot_candidates", {}).keys()),
            "stale_check": blindspot.blindspot_audit_stale(skill_name)
        }
    except ImportError:
        ctx["blindspots"] = {"error": "blindspot module not available"}

    # ── 10. Design params ──
    try:
        from engine.loop import _load_params
        ctx["design_params"] = _load_params(skill_name)
    except ImportError:
        ctx["design_params"] = {}

    # ── 11. Claude's previous verdicts (if any) ──
    try:
        from engine import verdict
        prev = verdict.read_recent_verdicts(skill_name, limit=20)
        ctx["previous_claude_verdicts"] = prev
    except ImportError:
        ctx["previous_claude_verdicts"] = []

    return ctx


def _describe_rules(active_rules: list, compact: bool = False) -> list:
    """Describe each active rule, including fix function source when available."""
    import inspect
    described = []
    for rule in active_rules:
        desc = {
            "name": rule.get("name", "?"),
            "pattern": rule.get("pattern", "?"),
            "auto_apply": rule.get("auto_apply", False),
            "severity": rule.get("severity", "?"),
            "description": rule.get("description", ""),
            "source": rule.get("_source", "seed"),
            "has_fix_function": "fix" in rule,
        }
        # Include fix function source code (Claude needs this to judge quality)
        if not compact and "fix" in rule:
            try:
                desc["fix_source"] = inspect.getsource(rule["fix"])
            except (OSError, TypeError):
                desc["fix_source"] = "# (unable to retrieve source)"
        elif "fix" in rule:
            desc["fix_source"] = "# (compact mode — use --no-compact for source)"

        # For generated rules without fix code, include the strategy
        if not desc["has_fix_function"]:
            desc["fix_strategy"] = rule.get("_fix_strategy", rule.get("fix_strategy", ""))[:300]
            desc["needs_code"] = rule.get("_needs_code", False)

        described.append(desc)
    return described


def _summarize_events(events: list) -> list:
    """Summarize recent events — keep structure but truncate large payloads."""
    summarized = []
    for e in events[:30]:
        data = e.get("data", {})
        # Truncate large text fields
        data_compact = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 500:
                data_compact[k] = v[:500] + "..."
            elif isinstance(v, list) and len(v) > 20:
                data_compact[k] = v[:20]
            elif isinstance(v, dict) and len(str(v)) > 1000:
                data_compact[k] = str(v)[:500] + "..."
            else:
                data_compact[k] = v

        summarized.append({
            "event_id": e.get("event_id", "?")[-20:],
            "type": e.get("event_type", "?"),
            "iso": e.get("iso_timestamp", "?")[:19],
            "data": data_compact
        })
    return summarized


def _calculate_fitness_snapshot(skill_name: str) -> dict:
    """Calculate fitness with component breakdown."""
    try:
        from engine import hillclimb
        fitness = hillclimb.calculate_unified_fitness(skill_name)

        # Get individual components for transparency
        last_findings = memory.get_latest_metric(skill_name, "total_findings", 0)
        gvu = memory.calculate_gvu_snr(skill_name)
        snr = gvu.get("verifier_snr", 0)
        if snr is None:
            snr = 0.5
        traces = [e for e in memory.read_events(skill_name, limit=50)
                  if e["event_type"] == "execution_trace"]
        trace_pass = (
            sum(1 for t in traces if t.get("data", {}).get("success", False)) /
            max(1, len(traces))
        ) if traces else 1.0

        return {
            "unified_fitness": fitness,
            "components": {
                "findings_normalized": max(0.0, 1.0 - last_findings / 20.0),
                "verifier_snr": snr,
                "trace_pass_rate": trace_pass,
                "generator_noise": gvu.get("generator_noise", 0),
                "stable": gvu.get("stable", True),
            },
            "verdict": gvu.get("verdict", "?")
        }
    except Exception as e:
        return {"error": f"fitness calculation failed: {e}"}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Context Builder v4 — 为 Claude 构建完整决策上下文"
    )
    parser.add_argument("skill", help="目标 skill 名称")
    parser.add_argument("--compact", action="store_true",
                        help="紧凑模式——跳过 fix 函数源码")
    parser.add_argument("--scan-only", action="store_true",
                        help="仅输出扫描发现")
    parser.add_argument("--rules-only", action="store_true",
                        help="仅输出活跃规则")
    parser.add_argument("--events-only", action="store_true",
                        help="仅输出最近事件")
    parser.add_argument("--output", default="-",
                        help="输出文件路径（默认 stdout）")

    args = parser.parse_args()

    ctx = build_context(args.skill, compact=args.compact)

    if "error" in ctx:
        print(json.dumps(ctx, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    if args.scan_only:
        output = ctx.get("findings", {})
    elif args.rules_only:
        output = ctx.get("rules", [])
    elif args.events_only:
        output = ctx.get("recent_events", [])
    else:
        output = ctx

    json_text = json.dumps(output, indent=2, ensure_ascii=False)

    if args.output == "-":
        print(json_text)
    else:
        Path(args.output).write_text(json_text, encoding="utf-8")
        print(f"[OK] Context written to {args.output}")


if __name__ == "__main__":
    main()
