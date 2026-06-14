#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S10: Anti-Gaming — detect metric gaming vs. genuine improvement (EST, 2025).

v1.2: Distilled from Evaluator Stress Test (arXiv 2507.05619) + Meerkat (2026).

Core problem (EST, 2025):
  "When an agent's reward is based on findings_count, it may learn to
   reduce findings_count WITHOUT improving quality — by masking severity,
   adding noise, or exploiting format quirks in the detection pipeline."

EST solves this with controlled perturbations: inject invariant changes
that SHOULD NOT affect the score. If the score changes, the metric is
being gamed.

Anti-gaming checks for skill-builder:
  G1: Severity masking — did critical findings decrease but warnings increase?
  G2: Noise flooding — did total findings drop but file count explode?
  G3: Pattern shift — did findings shift from fixable to unfixable patterns?
  G4: Round inflation — did convergence happen but rounds dropped suspiciously fast?
"""

import json
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def check_severity_masking(skill_name: str, lookback: int = 10) -> dict:
    """G1: Did critical findings decrease while warnings increased?

    Genuine fix: both critical AND warning decline.
    Masking: critical drops but warnings rise (renamed, not fixed).
    """
    from engine import memory
    events = memory.read_events(skill_name, limit=lookback * 5)
    scans = [e for e in events if e["event_type"] == "scan"]
    if len(scans) < 6:
        return {"gaming_detected": False, "reason": "insufficient data"}

    half = len(scans) // 2
    early = scans[half:]
    recent = scans[:half]

    early_crit = sum(e.get("data", {}).get("findings_count", {}).get("critical", 0)
                     if isinstance(e.get("data", {}).get("findings_count"), dict)
                     else 1 if e.get("data", {}).get("findings", [{}])[0].get("severity") == "critical"
                     else 0 for e in early)
    recent_crit = sum(e.get("data", {}).get("findings_count", 0)
                      if isinstance(e.get("data", {}).get("findings_count"), (int, float))
                      else 0 for e in recent)

    # Approximate: count severity changes from findings lists
    def count_by_sev(events_list, sev):
        cnt = 0
        for e in events_list:
            for f in e.get("data", {}).get("findings", []):
                if f.get("severity") == sev:
                    cnt += 1
        return cnt

    early_crit = count_by_sev(early, "critical")
    recent_crit = count_by_sev(recent, "critical")
    early_warn = count_by_sev(early, "warning")
    recent_warn = count_by_sev(recent, "warning")

    crit_dropped = early_crit > recent_crit * 1.5 if recent_crit > 0 else early_crit > 0
    warn_rose = recent_warn > early_warn * 1.5 if early_warn > 0 else False
    masking = crit_dropped and warn_rose

    return {
        "gaming_detected": masking,
        "critical_early": early_crit,
        "critical_recent": recent_crit,
        "warning_early": early_warn,
        "warning_recent": recent_warn,
        "signal": "severity_masking" if masking else "none",
        "explanation": (
            "critical findings dropped but warnings rose proportionally — "
            "possible severity masking (renaming critical to warning)" if masking
            else "severity distribution normal"
        )
    }


def check_noise_flooding(skill_name: str, lookback: int = 10) -> dict:
    """G2: Did findings drop but file count explode?

    Genuine fix: both findings AND affected files decrease.
    Flooding: findings per file drops (dilution) but total files scanned explodes.
    """
    from engine import memory
    events = memory.read_events(skill_name, limit=lookback * 5)
    scans = [e for e in events if e["event_type"] == "scan"]
    if len(scans) < 6:
        return {"gaming_detected": False, "reason": "insufficient data"}

    half = len(scans) // 2
    early = scans[half:]
    recent = scans[:half]

    # Count unique files per scan
    def avg_files(events_list):
        files_per_scan = []
        for e in events_list:
            files = set(f.get("file", "?") for f in e.get("data", {}).get("findings", []))
            files_per_scan.append(len(files))
        return sum(files_per_scan) / max(1, len(files_per_scan))

    def avg_findings(events_list):
        return sum(e.get("data", {}).get("findings_count",
                  len(e.get("data", {}).get("findings", [])))
                  for e in events_list) / max(1, len(events_list))

    early_files = avg_files(early)
    recent_files = avg_files(recent)
    early_findings = avg_findings(early)
    recent_findings = avg_findings(recent)

    files_exploded = recent_files > early_files * 2
    findings_per_file_dropped = (recent_findings / max(1, recent_files)) < \
                                 (early_findings / max(1, early_files)) * 0.5
    flooding = files_exploded and findings_per_file_dropped

    return {
        "gaming_detected": flooding,
        "files_early": round(early_files, 1),
        "files_recent": round(recent_files, 1),
        "findings_early": round(early_findings, 1),
        "findings_recent": round(recent_findings, 1),
        "signal": "noise_flooding" if flooding else "none",
        "explanation": (
            "files scanned exploded while findings per file dropped — "
            "possible noise flooding (diluting real issues)" if flooding
            else "file distribution normal"
        )
    }


def check_pattern_shift(skill_name: str, lookback: int = 10) -> dict:
    """G3: Did fixable findings decrease but unfixable findings stay?

    Gaming: system learns to fix only the easy (auto-fixable) patterns,
    while hard patterns persist or increase. findings_count drops but
    quality doesn't improve proportionally.
    """
    from engine import memory
    events = memory.read_events(skill_name, limit=lookback * 5)
    scans = [e for e in events if e["event_type"] == "scan"]
    if len(scans) < 6:
        return {"gaming_detected": False, "reason": "insufficient data"}

    half = len(scans) // 2
    early = scans[half:]
    recent = scans[:half]

    fixable_patterns = {"grep-pcre", "missing-utf8-header", "hardcoded-versions",
                         "dev-null", "missing-docstring"}

    def count_by_fixability(events_list, fixable=True):
        cnt = 0
        for e in events_list:
            for f in e.get("data", {}).get("findings", []):
                is_fixable = f.get("pattern", "") in fixable_patterns
                if is_fixable == fixable:
                    cnt += 1
        return cnt

    early_fixable = count_by_fixability(early, True)
    recent_fixable = count_by_fixability(recent, True)
    early_unfixable = count_by_fixability(early, False)
    recent_unfixable = count_by_fixability(recent, False)

    fixable_dropped = early_fixable > 0 and recent_fixable < early_fixable * 0.3
    unfixable_same = early_unfixable > 0 and recent_unfixable >= early_unfixable * 0.7
    pattern_shift = fixable_dropped and unfixable_same

    return {
        "gaming_detected": pattern_shift,
        "fixable_early": early_fixable,
        "fixable_recent": recent_fixable,
        "unfixable_early": early_unfixable,
        "unfixable_recent": recent_unfixable,
        "signal": "pattern_shift" if pattern_shift else "none",
        "explanation": (
            "fixable findings dropped but unfixable persisted — "
            "possible cherry-picking (only fixing easy problems)" if pattern_shift
            else "pattern distribution normal"
        )
    }


def run_anti_gaming_check(skill_name: str) -> dict:
    """Run all anti-gaming checks. Returns aggregate gaming risk.

    Gaming risk level:
      none = all clean
      low = 1 signal (possible coincidence)
      medium = 2 signals (likely systematic gaming)
      high = 3 signals (almost certainly gaming the metrics)
    """
    checks = [
        check_severity_masking(skill_name),
        check_noise_flooding(skill_name),
        check_pattern_shift(skill_name),
    ]

    signals = [c for c in checks if c.get("gaming_detected")]
    risk = ("none" if len(signals) == 0
            else "low" if len(signals) == 1
            else "medium" if len(signals) == 2
            else "high")

    if risk in ("medium", "high"):
        from engine import memory
        memory.write_insight(
            skill_name,
            f"S10 Anti-Gaming: {len(signals)} 个奖励黑客信号检测到。"
            f"风险级别: {risk}。信号: {[s.get('signal') for s in signals]}",
            [], confidence=0.6 + len(signals) * 0.1
        )

    return {
        "total_checks": len(checks),
        "signals_detected": len(signals),
        "signal_details": [s for s in checks if s.get("gaming_detected")],
        "risk_level": risk,
        "convergence_trustworthy": risk == "none",
        "verdict": (
            f"[OK] S10: 无奖励黑客信号" if risk == "none"
            else f"[!!] S10: {len(signals)} 个奖励黑客信号 (风险={risk})"
        )
    }


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="S10 Anti-Gaming — metric gaming detection (EST 2025, Meerkat 2026)")
    p.add_argument("skill", help="目标 skill")
    args = p.parse_args()

    result = run_anti_gaming_check(args.skill)
    print(f"  {result['verdict']}")
    for s in result.get("signal_details", []):
        print(f"    [{s.get('signal', '?')}] {s.get('explanation', '?')}")
    if result["convergence_trustworthy"]:
        print(f"  → 收敛可信——未检测到指标操控")
    else:
        print(f"  → 收敛可能为假——需要人工审查")

if __name__ == "__main__":
    main()
