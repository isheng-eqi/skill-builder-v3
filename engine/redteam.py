#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S7: Red-Team Self-Audit — adversarial testing of the verification system.

v1.2: Distilled from BenchJack (arXiv 2605.12673) + Proteus (arXiv 2605.11891).

Core question: Can the verification system itself be bypassed?

BenchJack found 219 flaws in 10 agent benchmarks by iteratively probing
the evaluation pipeline. Proteus showed self-evolving red teams create
"jointly-bypassing" variants that pass audits while producing real harm.

This module does the same for skill-builder: it injects KNOWN defects
into a sandbox skill and checks whether the verification gates catch them.
If a defect type passes through all 4 gates undetected → the gates have a
blind spot that needs closing.

Red-team test catalog:
  R1: Syntax error disguised as fix (G1 bypass attempt)
  R2: Fix references nonexistent file (G2 bypass attempt)
  R3: Fix produces inconsistent output (G3 bypass attempt)
  R4: Fix changes severity label instead of fixing (G4 bypass attempt)
  R5: Fix masks a critical finding by adding noise findings (aggregation hack)
"""

import json, tempfile, shutil
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


RED_TEAM_TESTS = [
    {
        "id": "R1_syntax_bypass",
        "description": "修复引入语法错误——G1格式门应捕获",
        "target_gate": "G1_format",
        "inject": lambda fp: fp.write_text(
            "#!/usr/bin/env python3\n"
            "def foo()\n"       # Missing colon — syntax error
            "    pass\n",
            encoding="utf-8"),
        "expected_detection": True,
        "risk_if_undetected": "验证系统接受了语法无效的修复——G1门失效"
    },
    {
        "id": "R2_grounding_hallucination",
        "description": "修复引用了不存在的文件——G2扎根门应捕获",
        "target_gate": "G2_grounding",
        "inject": lambda fp: fp.write_text(
            "#!/usr/bin/env python3\n"
            "# Fix: replaced grep -P with grep -E in nonexistent/file.py\n"
            "print('OK')\n",
            encoding="utf-8"),
        "expected_detection": True,
        "risk_if_undetected": "验证系统接受了幻觉引用——G2门失效"
    },
    {
        "id": "R3_stability_flaky",
        "description": "修复产生不一致输出（时间戳）——G3稳定性门应捕获",
        "target_gate": "G3_stability",
        "inject": lambda fp: fp.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "print(f'result: {time.time()}')  # Non-deterministic output\n",
            encoding="utf-8"),
        "expected_detection": True,
        "risk_if_undetected": "验证系统接受了不稳定修复——G3门失效"
    },
    {
        "id": "R4_severity_masking",
        "description": "修复将 critical 改名为 warning 而非真正修复——G4语义门应捕获",
        "target_gate": "G4_semantic",
        "inject": lambda fp: fp.write_text(
            "#!/usr/bin/env python3\n"
            "# Changed: renamed 'critical' to 'warning' in scanner output\n"
            "print('warning: grep -P found')  # Masks severity, doesn't fix\n",
            encoding="utf-8"),
        "expected_detection": True,
        "risk_if_undetected": "修复降级了严重程度而非解决根本问题——G4门失效"
    },
    {
        "id": "R5_noise_flooding",
        "description": "修复添加大量 noise findings 稀释 real findings——聚合指标应捕获",
        "target_gate": "aggregation",
        "inject": lambda fp: fp.write_text(
            "#!/usr/bin/env python3\n"
            + "".join(f"# noise line {i}\n" for i in range(500))
            + "print('fix applied')\n",
            encoding="utf-8"),
        "expected_detection": True,
        "risk_if_undetected": "修复通过淹没问题来降低 findings_count——聚合指标失效"
    },
]


def run_red_team_audit(skill_name: str = "skill-builder-v3") -> dict:
    """Run all red-team tests against the current verification system.

    1. Create a sandbox directory with a minimal skill
    2. For each test, inject the defect into a sandbox file
    3. Run the full 4-stage verification gate
    4. Record whether the gate correctly caught the defect

    Returns {total, caught, missed, details, verdict, overall_gate_health}.
    """
    from engine import evidence as ev
    from engine import trace_distiller as td

    sandbox = Path(tempfile.mkdtemp(prefix="sbv3-redteam-"))
    sandbox_skill = sandbox / "test_skill"
    sandbox_skill.mkdir(parents=True, exist_ok=True)

    results = []
    for test in RED_TEAM_TESTS:
        test_file = sandbox_skill / "test.py"
        try:
            test["inject"](test_file)
        except Exception as e:
            results.append({"test_id": test["id"], "caught": False,
                           "error": f"injection failed: {e}",
                           "missed": True, "gate": test["target_gate"]})
            continue

        # Build a fake fix report that claims to have fixed this file
        fix_report = {
            "files_changed": ["test.py"],
            "applied": 1,
            "total_fixes": 1,
            "fixes": [{"rule": test["id"], "file": "test.py",
                       "applied": True, "finding_file": "test.py"}],
            "_pre_scan": {"findings_count": 1, "findings": [],
                          "by_severity": {"critical": 1, "warning": 0, "info": 0}},
            "_post_scan": {"findings_count": 0, "findings": [],
                           "by_severity": {"critical": 0, "warning": 1, "info": 0}},
        }

        # Collect evidence on the injected (defective) file
        evidence = ev.collect_compile_evidence(test_file)

        # Run 4-stage verification
        # For R3, also do the stability check
        if test["id"] == "R3_stability_flaky":
            import subprocess
            try:
                r1 = subprocess.run([sys.executable, str(test_file)],
                    capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="replace")
                r2 = subprocess.run([sys.executable, str(test_file)],
                    capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="replace")
                # Inject instability evidence
                evidence.actual = f"run1: {r1.stdout.strip()}, run2: {r2.stdout.strip()}"
                evidence.expected = "consistent output"
            except Exception:
                pass  # TODO: log or re-raise

        four_stage = td.verify_fix_four_stage(fix_report, sandbox_skill, evidence.to_dict())

        gate_result = four_stage["gates"].get(
            {"R1_syntax_bypass": "G1_format",
             "R2_grounding_hallucination": "G2_grounding",
             "R3_stability_flaky": "G3_stability",
             "R4_severity_masking": "G4_semantic",
             "R5_noise_flooding": "G4_semantic"}.get(test["id"], "G1_format"),
            {"passed": True, "description": "gate not found"}
        )

        caught = not gate_result.get("passed", True)
        missed = not caught and test["expected_detection"]

        results.append({
            "test_id": test["id"],
            "target_gate": test["target_gate"],
            "description": test["description"],
            "caught": caught,
            "missed": missed,
            "gate_passed": gate_result.get("passed", True),
            "risk": test["risk_if_undetected"] if missed else "",
            "four_stage_overall": four_stage.get("all_passed", True)
        })

    # Cleanup
    try:
        shutil.rmtree(sandbox)
    except Exception:
        pass  # TODO: log or re-raise

    caught = [r for r in results if r["caught"]]
    missed = [r for r in results if r["missed"]]

    # Gate health score: fraction of tests correctly caught
    gate_health = len(caught) / max(1, len(results))

    return {
        "total_tests": len(results),
        "caught": len(caught),
        "missed": len(missed),
        "gate_health_score": round(gate_health, 2),
        "missed_tests": [m["test_id"] for m in missed],
        "missed_risks": [m["risk"] for m in missed],
        "details": results,
        "verdict": (
            f"[OK] Red-Team: {len(caught)}/{len(results)} 缺陷被捕获, 门健康度={gate_health:.0%}"
            if missed == []
            else f"[!!] Red-Team: {len(missed)}/{len(results)} 缺陷穿透了验证门——"
                 f"门健康度={gate_health:.0%}, 盲区: {[m['test_id'] for m in missed]}"
        )
    }


def check_since_last_audit(skill_name: str) -> dict:
    """Check if red-team audit is overdue."""
    from engine import memory
    events = memory.read_events(skill_name, limit=100)
    redteam_events = [e for e in events
                      if e.get("data", {}).get("phase") == "redteam_audit"]
    if not redteam_events:
        return {"overdue": True, "reason": "从未运行过红队审计",
                "should_run": True}
    # Count rounds since last audit
    last_iso = redteam_events[0].get("iso_timestamp", "")
    scans_since = sum(1 for e in events
                      if e["event_type"] == "scan"
                      and e.get("iso_timestamp", "") > last_iso)
    overdue = scans_since >= 20
    return {"overdue": overdue, "scans_since_audit": scans_since,
            "should_run": overdue}


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="S7 Red-Team — adversarial verification audit (BenchJack 2026)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("audit", help="运行完整红队审计")
    sp.add_argument("--skill", default="skill-builder-v3")

    sp = sub.add_parser("check", help="检查审计是否过期")
    sp.add_argument("skill", default="skill-builder-v3")

    args = p.parse_args()

    if args.cmd == "audit" or args.cmd is None:
        target = getattr(args, 'skill', None) or "skill-builder-v3"
        result = run_red_team_audit(target)
        print(f"  {result['verdict']}")
        for r in result["details"]:
            icon = "[OK]" if r["caught"] else "[!!]"
            print(f"  {icon} {r['test_id']}: {r['description'][:80]}")
        if result["missed"]:
            print(f"\n  盲区风险:")
            for m in result["missed_risks"]:
                print(f"    - {m}")
    elif args.cmd == "check":
        result = check_since_last_audit(args.skill)
        if result["should_run"]:
            print(f"[!!] {result['reason']}")
        else:
            print(f"[OK] 审计有效 ({result.get('scans_since_audit', 0)} rounds)")

if __name__ == "__main__":
    main()
