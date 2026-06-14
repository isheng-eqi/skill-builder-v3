#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S9: Skill Composer — spontaneous compound rule synthesis (SkillPyramid, 2026).

v1.2: Distilled from SkillPyramid (arXiv 2606.03692) + PolySkill (ICLR 2026).

Core insight (SkillPyramid, 2026):
  "Agents should compose, validate, and incorporate new skills DURING
   task execution — not wait for post-hoc analysis. This transforms a
   static skill pool into a dynamic evolution system."

PolySkill (ICLR 2026) adds: decouple a skill's abstract GOAL from its
concrete IMPLEMENTATION. Skills that share goals but differ in
implementation can be composed via polymorphism.

For skill-builder, skill composition means:
  Two rules that frequently co-trigger on the SAME file can be
  combined into a compound rule that handles both in one pass.
  → Fewer scans, fewer fix passes, less interference.

Compound rule format:
  {name: "compound:ruleA+ruleB", triggers: [patternA, patternB],
   files: common_files, co_occurrence_rate: float,
   fix_strategy: "chain" | "parallel",
   status: "proposed" | "active" | "deprecated"}
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def _compositions_path(skill_name: str) -> Optional[Path]:
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            cd = d / "data" / "compositions"
            cd.mkdir(parents=True, exist_ok=True)
            return cd / "compound_rules.json"
    return None


def analyze_co_occurrence(skill_name: str, lookback_rounds: int = 10) -> dict:
    """Analyze which rule pairs frequently co-fire on the same file.

    Co-occurrence = two rules triggered by findings in the SAME file
    within the same scan round. High co-occurrence suggests the rules
    are addressing related problems in the same file — a compound rule
    could handle both at once.
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=lookback_rounds * 10)
    scan_events = [e for e in events if e["event_type"] == "scan"]

    # Build co-occurrence matrix: (ruleA, ruleB) -> count
    co_occurrence = defaultdict(int)
    rule_frequencies = defaultdict(int)

    for scan in scan_events:
        findings = scan.get("data", {}).get("findings", [])
        # Group findings by file
        by_file = defaultdict(list)
        for f in findings:
            pattern = f.get("pattern", "unknown")
            ffile = f.get("file", "?")
            by_file[ffile].append(pattern)
            rule_frequencies[pattern] += 1

        # For each file with multiple findings, record all pairs
        for ffile, patterns in by_file.items():
            if len(patterns) < 2:
                continue
            seen = set()
            for i, p1 in enumerate(patterns):
                for p2 in patterns[i+1:]:
                    if p1 == p2:
                        continue
                    key = tuple(sorted([p1, p2]))
                    if key not in seen:
                        co_occurrence[key] += 1
                        seen.add(key)

    # Filter to significant pairs (co-occurred 3+ times)
    significant = {}
    for (p1, p2), count in co_occurrence.items():
        if count >= 3:
            p1_freq = rule_frequencies.get(p1, 1)
            p2_freq = rule_frequencies.get(p2, 1)
            co_rate = count / max(1, min(p1_freq, p2_freq))
            significant[f"{p1}+{p2}"] = {
                "rule_a": p1,
                "rule_b": p2,
                "co_occurrence_count": count,
                "co_occurrence_rate": round(co_rate, 2),
                "rule_a_frequency": p1_freq,
                "rule_b_frequency": p2_freq
            }

    return {
        "total_scan_events": len(scan_events),
        "significant_pairs": len(significant),
        "pairs": significant,
        "verdict": (
            f"[OK] 发现 {len(significant)} 个高频共现规则对——适合合成复合规则"
            if significant
            else "[OK] 无足够共现——规则足够独立"
        )
    }


def propose_compound_rule(skill_name: str, pair_name: str,
                           pair_data: dict) -> dict:
    """Propose a compound rule from a co-occurring pair.

    The compound rule: when BOTH patterns are likely to appear in the
    same file, apply fixes in sequence (chain) or simultaneously (parallel).
    """
    compound_name = f"compound:{pair_name}"

    strategy = "chain"  # Default: sequential fix (safer)
    # If both rules are simple text replacements, can do parallel
    if any(kw in pair_data["rule_a"] for kw in ["grep", "header", "utf8"]):
        strategy = "parallel"

    compound = {
        "name": compound_name,
        "rule_a": pair_data["rule_a"],
        "rule_b": pair_data["rule_b"],
        "co_occurrence_rate": pair_data["co_occurrence_rate"],
        "co_occurrence_count": pair_data["co_occurrence_count"],
        "fix_strategy": strategy,
        "status": "proposed",
        "iso_proposed": datetime.now(timezone.utc).isoformat(),
        "probation_required": 3,  # Fewer trials — rules are already proven individually
        "description": (
            f"复合规则: {pair_data['rule_a']} + {pair_data['rule_b']} "
            f"(共同触发率 {pair_data['co_occurrence_rate']:.0%}, "
            f"{pair_data['co_occurrence_count']} 次). "
            f"策略: {'并行修复' if strategy == 'parallel' else '顺序修复'}."
        )
    }

    # Save
    cp_path = _compositions_path(skill_name)
    if cp_path:
        existing = []
        if cp_path.exists():
            try:
                existing = json.loads(cp_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # TODO: log or re-raise
        # Avoid duplicates
        if not any(c.get("name") == compound_name for c in existing):
            existing.append(compound)
            cp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False),
                               encoding="utf-8")

    # Record event
    from engine import memory
    memory.write_event(skill_name, "propose", {
        "phase": "compound_rule_proposed",
        "compound_name": compound_name,
        "co_occurrence_rate": pair_data["co_occurrence_rate"]
    })

    return compound


def auto_synthesize_compounds(skill_name: str) -> dict:
    """Full auto-synthesis: analyze → propose → save.

    Run this periodically (every 5-10 rounds). Only proposes compounds
    for pairs with co-occurrence rate > 0.5 (they trigger together more
    than half the time).
    """
    analysis = analyze_co_occurrence(skill_name)

    created = 0
    for pair_name, pair_data in analysis["pairs"].items():
        if pair_data["co_occurrence_rate"] < 0.5:
            continue
        compound = propose_compound_rule(skill_name, pair_name, pair_data)
        created += 1

    return {
        "pairs_analyzed": analysis["significant_pairs"],
        "compounds_proposed": created,
        "analysis": analysis,
        "verdict": (
            f"[OK] S9: {created} 个复合规则自动合成"
            if created > 0
            else "[OK] S9: 无足够高共现对——规则独立性良好"
        )
    }


def list_compounds(skill_name: str) -> list:
    cp = _compositions_path(skill_name)
    if not cp or not cp.exists():
        return []
    try:
        return json.loads(cp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="S9 Skill Composer — compound rule synthesis (SkillPyramid/PolySkill 2026)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("analyze", help="分析规则共现模式")
    sp.add_argument("skill", help="目标 skill")
    sp.add_argument("--lookback", type=int, default=10)

    sp = sub.add_parser("synthesize", help="自动合成复合规则")
    sp.add_argument("skill", help="目标 skill")

    sp = sub.add_parser("list", help="列出已有复合规则")
    sp.add_argument("skill", help="目标 skill")

    args = p.parse_args()

    if args.cmd == "analyze":
        result = analyze_co_occurrence(args.skill, args.lookback)
        print(f"  {result['verdict']}")
        for name, data in result.get("pairs", {}).items():
            print(f"    [{name}] co={data['co_occurrence_rate']:.0%} "
                  f"({data['co_occurrence_count']}x)")
    elif args.cmd == "synthesize":
        result = auto_synthesize_compounds(args.skill)
        print(f"  {result['verdict']}")
    elif args.cmd == "list":
        compounds = list_compounds(args.skill)
        for c in compounds:
            print(f"  [{c['status']}] {c['name']}: {c.get('description', '?')[:120]}")
        if not compounds:
            print("  (无合成规则)")
    else:
        p.print_help()

if __name__ == "__main__":
    main()
