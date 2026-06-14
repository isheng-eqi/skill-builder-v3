#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S8: Regression Guard — capability degradation detection (CPE, 2026).

v1.2: Distilled from CPE (arXiv 2605.09315) + MoLEM (arXiv 2605.21951) +
      "Useful Memories Become Faulty" (arXiv 2605.12978).

Core finding (CPE, 2026):
  "Self-evolving agents consistently lose previously-acquired capabilities
   across ALL evolution channels — workflow, skill, model, memory."

Golden Test Suite: a set of known defects that the system SHOULD detect.
After every engine change, run the suite. If previously-detectable defects
are now missed → capability has regressed → reject the change and rollback.

The golden suite is intentionally minimal: 5 canonical defects that every
version of scanner+rules MUST catch. If these slip through, the system
has suffered catastrophic forgetting.
"""

import json, tempfile, shutil
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# Golden test suite: 5 defects that MUST be detected
GOLDEN_TESTS = [
    {
        "id": "GOLDEN_grep_pcre",
        "file": "README.md",
        "content": "# Guide\n\nTo search, use: `grep -P 'pattern' file.txt`\n",
        "expected_pattern": "grep-pcre",
        "severity": "critical",
        "description": "grep -P in root-level markdown docs must be detected"
    },
    {
        "id": "GOLDEN_dev_null",
        "file": "scripts/check.py",
        "content": "import os\nwith open('/dev/null', 'w') as f: f.write('test')\n",
        "expected_pattern": "dev-null",
        "severity": "warning",
        "description": "/dev/null 硬编码——Windows上不可用"
    },
    {
        "id": "GOLDEN_missing_utf8",
        "file": "scripts/main.py",
        "content": (
            "#!/usr/bin/env python3\n"
            "# Missing: import sys; sys.stdout.reconfigure(encoding='utf-8')\n"
            "print('hello')\n"
        ),
        "expected_pattern": "missing-utf8-header",
        "severity": "critical",
        "description": "Python脚本缺少UTF-8 stdout头"
    },
    {
        "id": "GOLDEN_hardcoded_version",
        "file": "scripts/setup.py",
        "content": "REQUIRED = 'python >= 3.8'\n"
                    "if sys.version_info < (3, 8): exit(1)\n",
        "expected_pattern": "hardcoded-versions",
        "severity": "warning",
        "description": "硬编码版本约束"
    },
    {
        "id": "GOLDEN_english_sentinel",
        "file": "scripts/report.py",
        "content": "#!/usr/bin/env python3\n"
                    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\n"
                    "print('error: file not found')\n"
                    "print('status: failed')\n",
        "expected_pattern": "language-drift",
        "severity": "warning",
        "description": "英文哨兵词检测——中文环境一致性"
    },
]


def setup_golden_sandbox() -> Path:
    """Create a sandbox skill with all golden defects."""
    sandbox = Path(tempfile.mkdtemp(prefix="sbv3-regression-"))
    skill_dir = sandbox / "golden_test"
    for subdir in ["scripts", "docs"]:
        (skill_dir / subdir).mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.md").write_text("# Golden Test Skill\n", encoding="utf-8")
    (skill_dir / "manifest.json").write_text(json.dumps({"name": "golden-test", "version": "1.0"}), encoding="utf-8")

    for test in GOLDEN_TESTS:
        fp = skill_dir / test["file"]
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(test["content"], encoding="utf-8")

    return skill_dir


def run_regression_suite(skill_name: str = "skill-builder-v3") -> dict:
    """Run the golden test suite against current scanner+rules.

    1. Create sandbox with known defects
    2. Run the current scanner against it
    3. Check: was each expected pattern detected?
    4. If any missed → capability regression

    Returns {total, detected, missed, capability_health, verdict}.
    """
    from engine.system1 import scanner

    sandbox = setup_golden_sandbox()

    # Monkey-patch: scanner needs to find this skill
    # Register it as a fake skill for the scan
    original_skill_root = scanner._find_skill_root
    def patched_find(name):
        if name == "golden_test":
            return sandbox
        return original_skill_root(name)
    scanner._find_skill_root = patched_find

    try:
        result = scanner.scan_skill("golden_test")
    finally:
        scanner._find_skill_root = original_skill_root
        try:
            shutil.rmtree(sandbox)
        except Exception:
            pass  # TODO: log or re-raise

    findings = result.get("findings", [])
    detected_patterns = set(f.get("pattern", "") for f in findings)

    results = []
    for test in GOLDEN_TESTS:
        detected = test["expected_pattern"] in detected_patterns
        results.append({
            "test_id": test["id"],
            "expected": test["expected_pattern"],
            "detected": detected,
            "severity": test["severity"],
            "description": test["description"]
        })

    detected = [r for r in results if r["detected"]]
    missed = [r for r in results if not r["detected"]]
    health = len(detected) / len(results) if results else 0

    return {
        "total_tests": len(results),
        "detected": len(detected),
        "missed": len(missed),
        "capability_health": round(health, 2),
        "missed_defects": [m["test_id"] for m in missed],
        "details": results,
        "verdict": (
            f"[OK] Regression Guard: {len(detected)}/{len(results)} golden defects detected, "
            f"capability health={health:.0%}"
            if missed == []
            else f"[!!] Regression Guard: {len(missed)}/{len(results)} golden defects MISSED — "
                 f"CAPABILITY DEGRADATION. 缺失: {[m['test_id'] for m in missed]}"
        ),
        "should_block": len(missed) > 0,
        "iso_tested": datetime.now(timezone.utc).isoformat()
    }


def guard_engine_change(files_changed: list, skill_name: str = "skill-builder-v3") -> dict:
    """Run regression guard before accepting engine changes.

    If any engine/scanner/rules file changed → MUST pass golden suite.
    If golden suite fails → BLOCK the change.

    This implements CPE: Capability-Preserving Evolution.
    """
    touches_critical = any(
        f.startswith("engine/system1/") or f == "engine/scanner.py"
        for f in files_changed
    ) or any(
        "scanner" in f or "rules" in f or "patterns" in f
        for f in files_changed
    )

    if not touches_critical:
        return {"blocked": False, "reason": "无关键检测文件变更——跳过回归检查",
                "should_run_suite": False}

    suite = run_regression_suite(skill_name)

    return {
        "blocked": suite["should_block"],
        "capability_health": suite["capability_health"],
        "suite_result": suite,
        "verdict": suite["verdict"],
        "should_run_suite": True
    }


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="S8 Regression Guard — golden test suite (CPE 2026)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("run", help="运行 golden test suite")
    sp = sub.add_parser("guard", help="检查引擎变更是否导致能力退化")
    sp.add_argument("--files", default="[]", help="JSON 变更文件列表")

    args = p.parse_args()

    if args.cmd == "guard":
        files = json.loads(args.files)
        result = guard_engine_change(files)
        print(f"  {result['verdict']}")
        if result["blocked"]:
            print(f"  → 拒绝变更——golden defects 未被检测到")
    else:
        result = run_regression_suite()
        print(f"  {result['verdict']}")
        for r in result["details"]:
            icon = "[OK]" if r["detected"] else "[!!]"
            print(f"  {icon} {r['test_id']}: {r['expected']} ({r['severity']})")

if __name__ == "__main__":
    main()
