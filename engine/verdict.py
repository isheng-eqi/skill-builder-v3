#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Verdict Recorder — Claude 独立判断记录器。

v4 架构翻转的核心：Python 的 auto-success（compile通过=修复成功）降级为
参考信号。Claude 的独立判断成为 ground truth。

每条修复经过 Claude 审查后，记录以下之一：
  GENUINE_FIX      — 修复真正改变了行为，问题被解决
  PSEUDO_FIX       — 修复只是加注释/TODO，行为未改变
  REGRESSION       — 修复引入了新问题
  NEEDS_IMPROVEMENT — 修复方向对但不完整
  SKIP             — 有意跳过（如 info 级别，不值得修）
  NEEDS_HUMAN      — 需要人工判断（涉及架构/安全）

Claude 的解释写入 data/claude_verdicts/，成为后续进化的真实信号。
TAPO、GVU、Hillclimb 的输入不再来自 Python 的 auto-success，
而是来自 Claude 的判断。

Usage:
  python engine/verdict.py <skill> --rule <name> --file <path> \\
      --verdict GENUINE_FIX --explanation "..." --evidence "..."
  python engine/verdict.py <skill> --list   # 列出最近判断
  python engine/verdict.py <skill> --stats  # 统计: 真修复 vs 伪修复 vs 回归
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from engine.platform import find_skill_root as _find_skill_root

# ── Verdict types ──────────────────────────────────────────────────────────────

VALID_VERDICTS = {
    "GENUINE_FIX":       "修复真正改变了行为——问题被解决",
    "GENUINE_FIX_PARTIAL": "修复部分解决了问题——还需补充",
    "PSEUDO_FIX":        "修复只是表面改动（加注释/TODO），行为未改变",
    "REGRESSION":        "修复引入了退化——代码变差了",
    "NO_EFFECT":         "修复无任何可观测效果",
    "SKIP":              "有意跳过——不值得修（info级别/已过时）",
    "NEEDS_HUMAN":       "需要人工判断——涉及架构/安全/宪法",
    "NEEDS_IMPROVEMENT": "修复方向对但不完整——fix函数需要改进",
}


def _verdicts_dir(skill_name: str) -> Optional[Path]:
    """Get the claude_verdicts directory."""
    root = _find_skill_root(skill_name)
    if not root:
        return None
    d = root / "data" / "claude_verdicts"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Record verdict ─────────────────────────────────────────────────────────────

def record_verdict(skill_name: str,
                   fix_rule: str,
                   fix_file: str,
                   verdict: str,
                   explanation: str,
                   evidence: str = "",
                   finding_pattern: str = "",
                   python_auto_verdict: str = "",
                   fix_function_notes: str = "") -> Optional[Path]:
    """Record Claude's independent judgment about a fix.

    Args:
        skill_name: 目标 skill
        fix_rule: 应用的规则名（如 'tag-bare-except'）
        fix_file: 被修复的文件
        verdict: GENUINE_FIX | PSEUDO_FIX | REGRESSION | ...
        explanation: Claude 的解释——为什么这么判断
        evidence: Claude 收集的证据（diff内容、测试输出等）
        finding_pattern: 对应的 finding pattern
        python_auto_verdict: Python 自己判的 success（参考信号）
        fix_function_notes: 对 fix 函数本身的评价

    Returns path to the recorded verdict file.
    """
    assert verdict in VALID_VERDICTS, (
        f"未知 verdict: {verdict}。有效值: {list(VALID_VERDICTS.keys())}"
    )

    vd = _verdicts_dir(skill_name)
    if not vd:
        return None

    ts = datetime.now(timezone.utc)
    entry = {
        "verdict_id": f"claude-v-{ts.strftime('%Y%m%d-%H%M%S-%f')}",
        "iso_recorded": ts.isoformat(),
        "skill": skill_name,
        "fix_rule": fix_rule,
        "fix_file": fix_file,
        "finding_pattern": finding_pattern,
        "verdict": verdict,
        "verdict_meaning": VALID_VERDICTS.get(verdict, "?"),
        "explanation": explanation,
        "evidence": evidence[:5000],
        "python_auto_verdict": python_auto_verdict,
        "fix_function_notes": fix_function_notes,
        # For future Claude sessions to read
        "is_ground_truth": True,
        "source": "Claude independent judgment (v4)"
    }

    fp = vd / f"{entry['verdict_id']}.json"
    fp.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also record as event for backward compatibility
    from engine import memory
    memory.write_event(skill_name, "fix", {
        "phase": "claude_verdict",
        "verdict_id": entry["verdict_id"],
        "fix_rule": fix_rule,
        "fix_file": fix_file,
        "verdict": verdict,
        "explanation": explanation[:500],
        "is_ground_truth": True
    })

    return fp


# ── Read verdicts ──────────────────────────────────────────────────────────────

def read_recent_verdicts(skill_name: str, limit: int = 20,
                          verdict_filter: Optional[str] = None) -> list:
    """Read Claude's recent verdicts. Newest first."""
    vd = _verdicts_dir(skill_name)
    if not vd:
        return []

    files = sorted(vd.glob("claude-v-*.json"), reverse=True)
    verdicts = []
    for fp in files:
        try:
            v = json.loads(fp.read_text(encoding="utf-8"))
            if verdict_filter and v.get("verdict") != verdict_filter:
                continue
            verdicts.append(v)
            if len(verdicts) >= limit:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return verdicts


# ── Statistics ─────────────────────────────────────────────────────────────────

def verdict_stats(skill_name: str) -> dict:
    """Calculate statistics: how many genuine fixes vs pseudo fixes vs regressions."""
    all_v = read_recent_verdicts(skill_name, limit=500)
    if not all_v:
        return {"total": 0, "verdict": "[--] 无 Claude 判断记录——系统尚未经过 LLM 审查"}

    counts = {}
    for v in all_v:
        vtype = v.get("verdict", "UNKNOWN")
        counts[vtype] = counts.get(vtype, 0) + 1

    total = len(all_v)
    genuine = counts.get("GENUINE_FIX", 0) + counts.get("GENUINE_FIX_PARTIAL", 0)
    pseudo = counts.get("PSEUDO_FIX", 0)
    regression = counts.get("REGRESSION", 0)
    no_effect = counts.get("NO_EFFECT", 0)

    # Key metric: genuine fix rate
    fix_rate = genuine / max(1, total)
    pseudo_rate = pseudo / max(1, total)
    regression_rate = regression / max(1, total)

    # Health assessment
    if fix_rate >= 0.7 and regression_rate == 0:
        health = "HEALTHY"
    elif pseudo_rate >= 0.5:
        health = "PSEUDO_TRAP"  # 系统在自我欺骗——大部分修复是假的
    elif regression_rate >= 0.2:
        health = "DEGRADING"   # 系统在退化
    elif total < 5:
        health = "INSUFFICIENT_DATA"
    else:
        health = "MIXED"

    return {
        "total": total,
        "counts": counts,
        "genuine_fix_rate": round(fix_rate, 3),
        "pseudo_fix_rate": round(pseudo_rate, 3),
        "regression_rate": round(regression_rate, 3),
        "health": health,
        "verdict": _health_verdict(health, fix_rate, pseudo_rate, regression_rate, genuine, pseudo, regression)
    }


def _health_verdict(health: str, fix_rate: float, pseudo_rate: float,
                     regression_rate: float, genuine: int, pseudo: int,
                     regression: int) -> str:
    """Generate human-readable health verdict."""
    if health == "HEALTHY":
        return f"[OK] 系统健康——{genuine} 次真修复, {genuine/(genuine+pseudo+regression):.0%} 真修复率"
    elif health == "PSEUDO_TRAP":
        return (f"[!!] 伪修复陷阱——{pseudo} 次伪修复 ({pseudo_rate:.0%})。"
                f"系统在加注释/加TODO，没有真正改变行为。"
                f"建议: 审查 fix 函数实现，将 tag-* 规则改为 detect-only")
    elif health == "DEGRADING":
        return (f"[!!] 系统退化——{regression} 次修复引入问题 ({regression_rate:.0%})。"
                f"建议: 暂停自动修复，检查最近改动的 fix 函数")
    elif health == "INSUFFICIENT_DATA":
        return f"[--] 数据不足——仅有 {genuine+pseudo+regression} 条判断"
    else:
        return f"[--] 混合状态——真修复={genuine}, 伪修复={pseudo}, 回归={regression}"


# ── Batch judgment helper ──────────────────────────────────────────────────────

def batch_judge_findings(skill_name: str, findings: list,
                          rules: list) -> list:
    """Generate a judgment template for Claude to fill in.

    This prints a structured template that Claude reads and fills with
    its independent judgments. Claude then calls record_verdict for each.
    """
    template = []
    active_rules = {r["pattern"]: r for r in rules}

    for i, f in enumerate(findings):
        pattern = f.get("pattern", "?")
        rule = active_rules.get(pattern, {})
        rule_name = rule.get("name", "NO_RULE")
        has_fix = "fix" in rule

        template.append({
            "index": i,
            "finding": {
                "pattern": pattern,
                "severity": f.get("severity", "?"),
                "file": f.get("file", "?"),
                "line": f.get("line", "?"),
                "description": f.get("description", ""),
                "match": f.get("match", "")[:100]
            },
            "rule": {
                "name": rule_name,
                "has_fix_function": has_fix,
                "auto_apply": rule.get("auto_apply", False),
                "description": rule.get("description", ""),
                "fix_source": rule.get("fix_source", "")[:1000] if has_fix else "",
            },
            # Claude fills these in:
            "claude_judgment": {
                "should_fix": None,   # True / False
                "verdict": None,      # GENUINE_FIX / PSEUDO_FIX / SKIP / ...
                "explanation": None,  # 为什么
                "evidence_to_collect": None,  # 要收集什么证据
                "fix_strategy_notes": None,   # fix 函数够不够好？不够好怎么改？
            }
        })

    return template


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verdict Recorder v4 — Claude 独立判断记录器"
    )
    sub = parser.add_subparsers(dest="cmd")

    # record
    p = sub.add_parser("record", help="记录 Claude 的判断")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--rule", required=True, help="规则名")
    p.add_argument("--file", required=True, help="修复的文件")
    p.add_argument("--verdict", required=True, choices=list(VALID_VERDICTS.keys()),
                   help="判断类型")
    p.add_argument("--explanation", required=True, help="为什么这么判断")
    p.add_argument("--evidence", default="", help="证据")
    p.add_argument("--pattern", default="", help="finding pattern")
    p.add_argument("--python-verdict", default="", help="Python 自动判断（参考）")
    p.add_argument("--fix-notes", default="", help="对 fix 函数的评价")

    # list
    p = sub.add_parser("list", help="列出最近判断")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--filter", choices=list(VALID_VERDICTS.keys()),
                   help="按类型筛选")

    # stats
    p = sub.add_parser("stats", help="判断统计")
    p.add_argument("skill", help="目标 skill")

    # template
    p = sub.add_parser("template", help="生成判断模板（供 Claude 填写）")
    p.add_argument("skill", help="目标 skill")

    args = parser.parse_args()

    if args.cmd == "record":
        fp = record_verdict(
            args.skill, args.rule, args.file, args.verdict,
            args.explanation, args.evidence, args.pattern,
            args.python_verdict, args.fix_notes
        )
        if fp:
            print(f"[OK] 判断已记录: {fp.name}")
        else:
            print(f"[!!] 记录失败——skill 不存在")
            sys.exit(1)

    elif args.cmd == "list":
        verdicts = read_recent_verdicts(args.skill, args.limit, args.filter)
        for v in verdicts:
            icon = {"GENUINE_FIX": "✅", "PSEUDO_FIX": "⚠️",
                    "REGRESSION": "❌", "NO_EFFECT": "➖",
                    "SKIP": "⏭️", "NEEDS_HUMAN": "👤",
                    "NEEDS_IMPROVEMENT": "🔧",
                    "GENUINE_FIX_PARTIAL": "🔨"}.get(v.get("verdict", ""), "❓")
            print(f"  {icon} [{v.get('verdict', '?')}] {v.get('fix_rule', '?')}")
            print(f"     文件: {v.get('fix_file', '?')}")
            print(f"     解释: {v.get('explanation', '')[:150]}")
            print()

    elif args.cmd == "stats":
        stats = verdict_stats(args.skill)
        print(f"  Claude 判断统计: {args.skill}")
        print(f"  总计: {stats['total']}")
        if stats["total"] > 0:
            for vtype, cnt in stats.get("counts", {}).items():
                icon = {"GENUINE_FIX": "✅", "PSEUDO_FIX": "⚠️",
                        "REGRESSION": "❌"}.get(vtype, "  ")
                print(f"    {icon} {vtype}: {cnt}")
            print(f"  真修复率: {stats['genuine_fix_rate']:.0%}")
            print(f"  伪修复率: {stats['pseudo_fix_rate']:.0%}")
            print(f"  回归率:   {stats['regression_rate']:.0%}")
            print(f"  健康度:   {stats['health']}")
        print(f"\n  {stats['verdict']}")

    elif args.cmd == "template":
        from engine import context as ctx_mod
        ctx = ctx_mod.build_context(args.skill)
        findings = ctx.get("findings", {}).get("items", [])
        rules = ctx.get("rules", [])
        template = batch_judge_findings(args.skill, findings, rules)
        print(json.dumps({
            "instruction": (
                "Claude: 对每个 finding 独立判断。查看 fix 函数源码（ctx.rules[].fix_source）。"
                "不要信任 Python 的 auto_apply——判断修复是否真正改变了行为。"
                "填写 claude_judgment 字段后，对每个判断调用:"
                "python engine/verdict.py <skill> record --rule <name> --file <path> "
                "--verdict <GENUINE_FIX|PSEUDO_FIX|...> --explanation '...'"
            ),
            "findings": template
        }, indent=2, ensure_ascii=False))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
