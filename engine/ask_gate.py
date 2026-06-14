#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S5: AskGate — knowing when NOT to self-repair (HiL-Bench Ask-F1, 2026).

HiL-Bench key finding:
  "Frontier coding agents collapse when specs are incomplete — not from
   capability gaps, but from JUDGMENT gaps. Agents predict 77% success
   while achieving 22%. Overconfidence is systemic, not accidental."

AskGate detects this overconfidence in real-time:
  1. Track self-verification success predictions vs actual independent outcomes
  2. If gap exceeds threshold (confidence >> reality) → pause self-improvement
  3. Escalate to human: "I think I fixed this, but my independent verifier
     disagrees 60% of the time. Stop trusting my own judgment."

This is NOT the same as GVU (which monitors fix quality vs noise ratio).
GVU asks "are fixes getting worse?" AskGate asks "do I know when I'm wrong?"
"""

from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def check_calibration(skill_name: str, latest_indep_result: Optional[dict] = None,
                       lookback: int = 20) -> dict:
    """Check if the agent is calibrated: does it know when it fails?

    Compares two signals over recent history:
      self_confidence: fraction of fixes the agent claimed as successful
      actual_accuracy: fraction of fixes the INDEPENDENT verifier confirmed

    Calibration gap = self_confidence - actual_accuracy.
    Positive gap = overconfident (agent thinks it's doing better than it is).
    Negative gap = underconfident (agent doubts itself too much).

    A gap > 0.4 (40 percentage points) triggers escalation to human.

    Returns {calibrated, confidence_gap, self_confidence, actual_accuracy,
             should_pause, escalate_to_human, verdict}.
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=lookback * 3)
    fix_events = [e for e in events if e["event_type"] == "fix"]
    exec_traces = [e for e in events if e["event_type"] == "execution_trace"]

    # Filter to traces from independent verification
    indep_traces = [
        t for t in exec_traces
        if t.get("data", {}).get("phase") in ("independent_verification", "verification")
    ]

    if len(fix_events) < 3:
        result = {
            "calibrated": True,
            "confidence_gap": 0.0,
            "self_confidence": 0.5,
            "actual_accuracy": 0.5,
            "should_pause": False,
            "escalate_to_human": False,
            "insufficient_data": True,
            "verdict": "[OK] AskGate: 数据不足 (<3 次修复事件)——跳过校准检查"
        }
        memory.write_metric(skill_name, "ask_gate_confidence_gap", 0.0,
                            {"self_confidence": 0.5, "actual_accuracy": 0.5,
                             "insufficient_data": True, "reason": "too few fix events"})
        return result

    # Filter to fix events that actually applied fixes (applied > 0)
    actionable_fixes = []
    for f in fix_events:
        data = f.get("data", {})
        report = data.get("fixes", data.get("fixes_report", data.get("fix_report", data)))
        if report.get("applied", 0) > 0:
            actionable_fixes.append(f)

    if len(actionable_fixes) < 3:
        result = {
            "calibrated": True,
            "confidence_gap": 0.0,
            "self_confidence": 0.5,
            "actual_accuracy": 0.5,
            "should_pause": False,
            "escalate_to_human": False,
            "insufficient_data": True,
            "verdict": f"[OK] AskGate: 有效修复不足 ({len(actionable_fixes)} 次 applied>0, 需要 ≥3)——跳过"
        }
        memory.write_metric(skill_name, "ask_gate_confidence_gap", 0.0,
                            {"self_confidence": 0.5, "actual_accuracy": 0.5,
                             "insufficient_data": True,
                             "reason": f"only {len(actionable_fixes)} actionable fixes"})
        return result

    # Self-confidence: fraction of fixes agent marked as "applied" and "success"
    self_reported = 0
    self_total = 0
    for f in actionable_fixes:
        data = f.get("data", {})
        # loop.py writes {"fixes": fix_report, "snapshot": ...}
        # legacy events may use "fixes_report" or "fix_report"
        report = data.get("fixes", data.get("fixes_report", data.get("fix_report", data)))
        applied = report.get("applied", 0)
        skipped = report.get("skipped", 0)
        total = applied + skipped
        if total > 0:
            self_reported += applied
            self_total += total
    self_confidence = self_reported / max(1, self_total)

    # Actual accuracy: fraction independent verifier confirmed
    verified_ok = sum(1 for t in indep_traces
                      if t.get("data", {}).get("success",
                         t.get("data", {}).get("agreement", False)))
    actual_accuracy = verified_ok / max(1, len(indep_traces))

    # If no independent traces yet, use GEPA trace data
    if len(indep_traces) < 3:
        all_traces = [t for t in exec_traces
                      if t.get("data", {}).get("phase") != "textual_gradient"]
        verified_ok = sum(1 for t in all_traces
                          if t.get("data", {}).get("success", False))
        actual_accuracy = verified_ok / max(1, len(all_traces))

    confidence_gap = self_confidence - actual_accuracy

    # Decision thresholds (from HiL-Bench Ask-F1 calibration)
    if confidence_gap > 0.4:
        should_pause = True
        escalate = True
        verdict = (f"[!!] S5: 严重过度自信——自称成功率 {self_confidence:.0%} "
                   f"实际 {actual_accuracy:.0%} (差距 {confidence_gap:.0%})")
    elif confidence_gap > 0.2:
        should_pause = True
        escalate = False
        verdict = (f"[!!] S5: 中度过度自信——自称 {self_confidence:.0%} "
                   f"实际 {actual_accuracy:.0%} (差距 {confidence_gap:.0%})")
    elif confidence_gap < -0.2:
        should_pause = False
        escalate = False
        verdict = (f"[--] S5: 过度保守——自称 {self_confidence:.0%} "
                   f"实际 {actual_accuracy:.0%} — agent 低估自己")
    else:
        should_pause = False
        escalate = False
        verdict = (f"[OK] S5: 校准良好——自称 {self_confidence:.0%} "
                   f"实际 {actual_accuracy:.0%} (差距 {confidence_gap:+.0%})")

    # Track calibration metric
    memory.write_metric(skill_name, "ask_gate_confidence_gap",
                        confidence_gap,
                        {"self_confidence": self_confidence,
                         "actual_accuracy": actual_accuracy,
                         "insufficient_data": False,
                         "samples": {"fix_events": len(fix_events),
                                     "independent_traces": len(indep_traces)}})

    return {
        "calibrated": abs(confidence_gap) < 0.2,
        "confidence_gap": round(confidence_gap, 3),
        "self_confidence": round(self_confidence, 3),
        "actual_accuracy": round(actual_accuracy, 3),
        "should_pause": should_pause,
        "escalate_to_human": escalate,
        "verdict": verdict,
        "samples": {"fix_events": len(fix_events),
                    "independent_traces": len(indep_traces)}
    }


# CLI
def main():
    import argparse
    p = argparse.ArgumentParser(
        description="S5 AskGate — calibration check (HiL-Bench Ask-F1, 2026)")
    p.add_argument("skill", help="Target skill name")
    args = p.parse_args()

    result = check_calibration(args.skill)
    print(f"  {result['verdict']}")
    print(f"  Self-confidence: {result['self_confidence']:.0%}")
    print(f"  Actual accuracy: {result['actual_accuracy']:.0%}")
    print(f"  Calibration gap: {result['confidence_gap']:+.0%}")
    if result["should_pause"]:
        print(f"  → 建议暂停自改进")
    if result["escalate_to_human"]:
        print(f"  → 建议人工校准")

if __name__ == "__main__":
    main()
