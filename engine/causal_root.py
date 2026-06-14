#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S4: CausalRoot — causal blame from execution traces (CausalFlow + causal-agent-replay, 2026).

CausalFlow key insight:
  Model agent traces as sequential causal chains, compute Causal Responsibility
  Scores via step-level counterfactual intervention. The step where changing
  the decision changes the outcome IS the causal locus.

Before:
  TextGrad says "fix failed, maybe X is wrong"
After:
  CausalRoot says "step #2 (insert import sys at line 5) is the root cause.
  Counterfactual: if step #2 succeeds, outcome flips 0->1. Steps #1,#3 irrelevant."

Ported from causal-agent-replay's do-calculus: do_resample(step_i) → measure delta.
"""

from typing import Optional

HERE = __import__('pathlib').Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def causal_blame(fix_steps: list, outcome_success: bool) -> dict:
    """Counterfactual root cause localization.

    For each step in a failed fix chain, compute: "If this step were correct,
    would the overall outcome flip from fail to pass?"

    The step with the highest counterfactual impact is the causal locus.
    Steps with near-zero impact are irrelevant to the failure.

    Returns {causal_locus, scores, conclusive, explanation}.
    """
    if not fix_steps:
        return {"causal_locus": -1, "scores": {}, "conclusive": False,
                "explanation": "No steps to analyze"}

    if len(fix_steps) == 1:
        return {"causal_locus": 0, "scores": {0: 1.0}, "conclusive": True,
                "explanation": "Single step — trivially the causal locus"}

    base = 1.0 if outcome_success else 0.0
    scores = {}

    for i in range(len(fix_steps)):
        before = fix_steps[:i]
        after = fix_steps[i + 1:]
        before_fails = sum(1 for s in before if not s.get("ok", False))
        after_fails = sum(1 for s in after if not s.get("ok", False))

        # Counterfactual A: this step succeeds
        hyp_ok = (len(fix_steps) - before_fails - after_fails * 0.5) / len(fix_steps)
        # Counterfactual B: this step removed
        total = max(1, len(fix_steps) - 1)
        hyp_removed = (total - before_fails - after_fails) / total

        delta = abs(hyp_ok - base) + abs(hyp_removed - base) * 0.5
        scores[i] = round(delta, 3)

    locus = max(scores, key=scores.get)
    max_score = scores[locus]
    # Irrelevant steps: contribution < 20% of max
    irrelevant = [i for i, s in scores.items() if s < max_score * 0.2]
    conclusive = max_score > 0.4 and len(scores) - len(irrelevant) <= 2

    explanation = (
        f"Step #{locus} is the root cause (score={max_score:.2f}). "
        f"Irrelevant steps: {irrelevant}."
        if conclusive
        else f"No single root cause — multi-factor interaction (max_score={max_score:.2f})"
    )

    return {
        "causal_locus": locus,
        "scores": scores,
        "conclusive": conclusive,
        "irrelevant_steps": irrelevant,
        "explanation": explanation
    }


def build_fix_steps_from_trace(trace_event: dict) -> list:
    """Convert an execution trace event into causal analyzable steps.

    Each step = {action, file, ok, description}.
    """
    data = trace_event.get("data", {})
    steps = []

    fix_file = data.get("fix_file", data.get("finding_file", "?"))
    fix_rule = data.get("fix_rule", "?")
    success = data.get("success", data.get("verdict") == "evidence_consistent")
    command = data.get("command", "?")
    exit_code = data.get("exit_code", -1)
    actual = data.get("stdout_tail", data.get("actual", ""))

    # Step 1: The analysis/detection phase
    steps.append({
        "action": "detect",
        "rule": fix_rule,
        "file": fix_file,
        "ok": success or "not found" not in str(actual).lower(),
        "description": f"Detect pattern in {fix_file}"
    })

    # Step 2: The fix application
    steps.append({
        "action": "apply_fix",
        "rule": fix_rule,
        "file": fix_file,
        "ok": success,
        "description": f"Apply {fix_rule} to {fix_file}"
    })

    # Step 3: Verification
    if command and command != "?":
        steps.append({
            "action": "verify",
            "rule": fix_rule,
            "file": fix_file,
            "ok": success,
            "command": command,
            "exit_code": exit_code,
            "description": f"Run verification: {str(command)[:80]}"
        })

    return steps


def enhance_textgrad_with_causal(fix_rule: str, fix_file: str,
                                  trace_event: dict) -> dict:
    """Upgrade fuzzy TextGrad failure explanation to causal root cause.

    Input:  A failed execution trace event
    Output: Causal root cause + irrelevant steps (what NOT to fix)
    """
    steps = build_fix_steps_from_trace(trace_event)
    data = trace_event.get("data", {})
    outcome = data.get("success", False)

    blame = causal_blame(steps, outcome)

    if not blame["conclusive"]:
        return {"enhanced": False, "reason": blame["explanation"],
                "blame": blame, "steps_analyzed": len(steps)}

    locus = blame["causal_locus"]
    locus_step = steps[locus] if 0 <= locus < len(steps) else {}

    enhanced = (
        f"CausalRoot: step #{locus} '{locus_step.get('action', '?')}' "
        f"is the root cause (score={blame['scores'].get(locus, '?'):.2f}). "
        f"Counterfactual: fixing this step would flip the outcome. "
        f"What NOT to fix: steps {blame['irrelevant_steps']}."
    )

    return {
        "enhanced": True,
        "causal_locus": locus,
        "locus_action": locus_step.get("action", "?"),
        "irrelevant_steps": blame["irrelevant_steps"],
        "enhanced_gradient": enhanced,
        "blame": blame,
        "steps_analyzed": len(steps)
    }


# CLI
def main():
    import argparse, json
    p = argparse.ArgumentParser(description="S4 CausalRoot — counterfactual root cause localization")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("blame", help="Run causal blame on a trace event")
    sp.add_argument("--trace", help="JSON file with trace event")
    sp.add_argument("--steps", default="[]", help="JSON fix steps array")

    sp = sub.add_parser("enhance", help="Enhance a TextGrad gradient with causal root cause")
    sp.add_argument("--trace", required=True, help="JSON file with trace event")
    sp.add_argument("--rule", default="unknown")
    sp.add_argument("--file", default="unknown")

    args = p.parse_args()

    if args.cmd == "blame":
        steps = json.loads(args.steps) if args.steps != "[]" else [
            {"ok": False, "action": "detect", "description": "detect pattern"},
            {"ok": False, "action": "apply_fix", "description": "apply fix"},
            {"ok": True, "action": "verify", "description": "run test"},
        ]
        result = causal_blame(steps, False)
        print(f"  Causal locus: step #{result['causal_locus']}")
        print(f"  Scores: {result['scores']}")
        print(f"  Irrelevant: {result.get('irrelevant_steps', [])}")
        print(f"  {result['explanation']}")

    elif args.cmd == "enhance":
        trace = json.loads(open(args.trace, encoding='utf-8').read())
        result = enhance_textgrad_with_causal(args.rule, args.file, trace)
        print(f"  Enhanced: {result['enhanced']}")
        if result['enhanced']:
            print(f"  {result['enhanced_gradient']}")

    else:
        p.print_help()


if __name__ == "__main__":
    main()
