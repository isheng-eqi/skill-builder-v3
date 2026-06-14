#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P13: Blindspot Detector — 对抗扫描覆盖率下降熵增.

v1.2 (2026-06-13): 基于宪法可实现性审计的诚实声明——扫描器只能检测8类已知问题，
但系统越复杂，未检测到的问题类越多。这是熵增最危险的形式。

机制:
  1. 收集 System 2 (deliberation) 发现但 System 1 scanner 无法检测的模式
  2. 同一新模式在最近 N 轮 deliberation 中出现 3+ 次
     → 自动标记为 "可能的新盲区"
     → 推送 rule_generator 生成候选规则
  3. 盲区审计状态过期(>10轮未审计)
     → 收敛判断改为 "零发现 且 盲区审计未过期"
     → 预防假收敛: scanner 说你没问题 ≠ 你真的没问题

盲区永远存在——诚实承认比假装全覆盖更有价值。
但这不意味着不能缩小盲区。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


KNOWN_BLINDSPOTS = [
    "API版本不兼容",
    "网络超时/DNS/代理",
    "并发/竞态条件",
    "配置文件格式漂移",
    "传递依赖冲突",
    "权限问题",
    "非UTF-8编码文件",
    "大文件/内存问题",
]

from engine.platform import find_skill_root as _find_skill_root
# ── Unruly pattern tracking ──────────────────────────────────────────────────

def collect_unruly_patterns(skill_name: str, lookback: int = 5) -> dict:
    """Collect patterns that System 2 found but System 1 couldn't handle.

    "Unruly" = findings without matching rules in the System 1 registry.
    These are the raw material for blind spot detection.

    Returns {patterns: {name: {count, last_seen_iso, affected_files}},
             total_unruly, blindspot_candidates}.
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=lookback * 10)
    scan_events = [e for e in events if e["event_type"] == "scan"]

    # Known System 1 patterns (hardcoded + any generated with pattern name)
    known_patterns = {
        "missing-utf8-header", "grep-pcre", "hardcoded-versions",
        "language-drift", "hardcoded-numbers", "missing-docstring",
        "self-reference", "dev-null", "distributed-state",
        "manifest-drift", "missing-declared-file", "homoiconicity-violation",
        "self-special-path", "missing-skill-md",
        "bare-except", "except-pass", "skill-md-oversize",
    }

    unruly = {}
    for e in scan_events[-lookback:]:
        findings = e.get("data", {}).get("findings", [])
        iso = e.get("iso_timestamp", "")

        for f in findings:
            pattern = f.get("pattern", "unknown")
            if pattern in known_patterns:
                continue
            if "global-insight:" in pattern:
                continue

            if pattern not in unruly:
                unruly[pattern] = {"count": 0, "last_seen_iso": iso,
                                   "affected_files": []}
            unruly[pattern]["count"] += 1
            unruly[pattern]["last_seen_iso"] = iso
            ffile = f.get("file", "?")
            if ffile not in unruly[pattern]["affected_files"][:10]:
                unruly[pattern]["affected_files"].append(ffile)

    # Blindspot candidates: unruly patterns appearing 3+ times
    candidates = {p: d for p, d in unruly.items() if d["count"] >= 3}

    return {
        "skill": skill_name,
        "scan_events_analyzed": len(scan_events[-lookback:]),
        "unruly_patterns": unruly,
        "unruly_count": len(unruly),
        "blindspot_candidates": candidates,
        "candidate_count": len(candidates),
        "verdict": (
            f"[!!] {len(candidates)} 个潜在盲区——"
            f"System 2 发现了 System 1 检测不到的模式"
            if candidates
            else "[OK] 无新的盲区候选"
        )
    }


# ── Blindspot audit ──────────────────────────────────────────────────────────

def _audit_state_path(skill_name: str) -> Optional[Path]:
    root = _find_skill_root(skill_name)
    if not root:
        return None
    return root / "data" / "blindspot_audit.json"


def run_blindspot_audit(skill_name: str, lookback: int = 5) -> dict:
    """Run a full blind spot audit. Writes state to disk.

    1. Collect unruly patterns from recent scans
    2. Identify ones appearing 3+ times
    3. For each candidate, check if a generated rule already exists
    4. If not → push to rule_generator for candidate rule creation
    5. Write audit state (iso_last_audit, candidates_found)

    Returns audit report.
    """
    from engine import memory

    unruly = collect_unruly_patterns(skill_name, lookback)
    candidates = unruly.get("blindspot_candidates", {})

    actions = []
    rules_created = 0

    if candidates:
        from engine.system2 import rule_generator

        for pattern_name, data in candidates.items():
            # Check if a rule already exists for this pattern
            existing = rule_generator.load_all_generated_rules(skill_name)
            already_covered = any(
                r.get("pattern") == pattern_name and r.get("status") != "deprecated"
                for r in existing
            )
            if already_covered:
                continue

            # Generate a candidate rule
            rule = rule_generator.generate_rule_from_stubborn_pattern(
                skill_name=skill_name,
                pattern_name=pattern_name,
                pattern_count=data["count"],
                affected_files=data.get("affected_files", []),
                related_traces=[],
                deliberation_text=(
                    f"盲区审计自动发现: {pattern_name} 在 {data['count']} 轮扫描中反复出现,"
                    f"但 System 1 scanner 无法检测。"
                )
            )
            if rule:
                rules_created += 1
                actions.append(
                    f"盲区→规则: {pattern_name} (出现{data['count']}次) → {rule['rule_id']}"
                )

    # Write audit state
    state_path = _audit_state_path(skill_name)
    audit_state = {
        "iso_last_audit": datetime.now(timezone.utc).isoformat(),
        "candidates_found": list(candidates.keys()),
        "candidate_count": len(candidates),
        "rules_created": rules_created,
        "known_blindspots_at_audit_time": KNOWN_BLINDSPOTS,
        "scanner_rules_at_audit": len(
            __import__("engine.system1.scanner", fromlist=["SCAN_PATTERNS"]).SCAN_PATTERNS
        ) if False else 8  # Fallback — scanner.SCAN_PATTERNS length is known
    }
    if state_path:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(audit_state, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    if actions:
        memory.write_insight(
            skill_name,
            f"盲区审计: 发现 {len(candidates)} 个候选盲区, "
            f"自动创建 {rules_created} 条候选规则. "
            f"模式: {list(candidates.keys())}",
            [], confidence=0.7
        )

    return {
        "skill": skill_name,
        "blindspot_candidates": len(candidates),
        "candidate_patterns": list(candidates.keys()),
        "rules_created": rules_created,
        "known_blindspots": KNOWN_BLINDSPOTS,
        "actions": actions,
        "verdict": (
            f"[OK] 盲区审计完成: {rules_created}条新规则从{rules_created or len(candidates)}个盲区生成"
            if candidates or rules_created
            else "[OK] 盲区审计完成——无新候选"
        )
    }


def blindspot_audit_stale(skill_name: str, max_age_rounds: int = 10) -> dict:
    """Check if the blind spot audit is stale (needs re-running).

    Returns {stale: bool, last_audit_rounds_ago, should_rerun}.

    If stale, convergence check in loop.py should NOT consider
    zero findings as "converged" — blind spots may be hiding problems.
    """
    from engine import memory

    state_path = _audit_state_path(skill_name)
    if not state_path or not state_path.exists():
        return {"stale": True, "reason": "从未运行过盲区审计",
                "should_rerun": True}

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last_iso = state.get("iso_last_audit", "")
    except (json.JSONDecodeError, OSError):
        return {"stale": True, "reason": "审计状态文件损坏",
                "should_rerun": True}

    # Count rounds since last audit
    events = memory.read_events(skill_name, limit=500)
    scan_count = len([e for e in events if e["event_type"] == "scan"])
    rounds_since = 0
    for e in events:
        if e["event_type"] in ("scan", "loop"):
            rounds_since += 1
        if e.get("iso_timestamp", "") <= last_iso:
            break

    stale = rounds_since >= max_age_rounds

    return {
        "stale": stale,
        "last_audit_iso": last_iso,
        "rounds_since_audit": rounds_since,
        "should_rerun": stale,
        "reason": (f"盲区审计已过期 ({rounds_since}轮未审计, 阈值{max_age_rounds})"
                   if stale else f"[OK] 审计有效 ({rounds_since}轮)")
    }


# ── Convergence guard ────────────────────────────────────────────────────────

def check_true_convergence(skill_name: str, findings_count: int,
                            consecutive_zero: int, stop_rounds: int,
                            max_audit_age: int = 10) -> dict:
    """Check if convergence is REAL or just a scanner blind spot.

    True convergence requires:
      1. consecutive_zero >= stop_rounds  (standard check)
      2. Blind spot audit is NOT stale    (coverage check)
      3. No new blind spot candidates     (novelty check)

    If condition 1 is met but 2 or 3 fails:
      → "假收敛" — system looks converged but scanner is blind
      → trigger blind spot audit instead of stopping

    Returns {truly_converged: bool, reason, should_audit: bool}.
    """
    # Standard convergence
    zero_ok = consecutive_zero >= stop_rounds

    if not zero_ok:
        return {"truly_converged": False, "reason": "尚未收敛",
                "should_audit": False}

    # Blind spot audit freshness
    staleness = blindspot_audit_stale(skill_name, max_audit_age)

    if staleness.get("stale"):
        return {
            "truly_converged": False,
            "reason": f"假收敛——零发现{consecutive_zero}轮, 但{staleness.get('reason','盲区审计已过期')}",
            "should_audit": True,
            "staleness": staleness
        }

    # Check for recent unruly patterns
    unruly = collect_unruly_patterns(skill_name, lookback=3)
    if unruly.get("candidate_count", 0) > 0:
        return {
            "truly_converged": False,
            "reason": f"假收敛——零发现但盲区审计发现 {unruly['candidate_count']} 个候选盲区: {list(unruly['blindspot_candidates'].keys())}",
            "should_audit": True,
            "candidates": list(unruly["blindspot_candidates"].keys())
        }

    return {
        "truly_converged": True,
        "reason": f"[OK] 真收敛——{consecutive_zero}轮零发现, 盲区审计有效",
        "should_audit": False
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P13 Blindspot Detector——扫描器盲区检测与假收敛预防"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("audit", help="运行盲区审计")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--lookback", type=int, default=5, help="回溯扫描轮数")
    p.add_argument("--create-rules", action="store_true",
                   help="自动为候选盲区生成规则")

    p = sub.add_parser("staleness", help="检查盲区审计是否过期")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--max-age", type=int, default=10, help="最大审计间隔")

    p = sub.add_parser("check-convergence", help="检查是否真收敛")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--consecutive-zero", type=int, default=0)
    p.add_argument("--stop-rounds", type=int, default=2)

    p = sub.add_parser("unruly", help="列出 System 1 无法处理的无规则模式")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--lookback", type=int, default=5)

    args = parser.parse_args()

    if args.cmd == "audit":
        result = run_blindspot_audit(args.skill, args.lookback)
        print(f"  {result['verdict']}")
        print(f"  候选盲区: {result['candidate_patterns']}")
        print(f"  已知盲区: {result['known_blindspots']}")
        for a in result.get("actions", []):
            print(f"    {a}")

    elif args.cmd == "staleness":
        result = blindspot_audit_stale(args.skill, args.max_age)
        print(f"  {'[!!]' if result['stale'] else '[OK]'} {result['reason']}")

    elif args.cmd == "check-convergence":
        conv = check_true_convergence(
            args.skill, 0, args.consecutive_zero, args.stop_rounds
        )
        icon = "[OK]" if conv["truly_converged"] else "[!!]"
        print(f"  {icon} {conv['reason']}")
        if conv.get("should_audit"):
            print(f"  → 建议: 运行盲区审计 (python engine/blindspot.py audit {args.skill})")
            if conv.get("candidates"):
                print(f"  → 候选盲区: {conv['candidates']}")

    elif args.cmd == "unruly":
        result = collect_unruly_patterns(args.skill, args.lookback)
        print(f"  {result['verdict']}")
        for pattern, data in result.get("blindspot_candidates", {}).items():
            print(f"    [{pattern}] x{data['count']}: {data.get('affected_files', [])}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
