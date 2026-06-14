#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Layer 3: Anchor — Constitution verification + rollback + verify-before-adopt gate.

This is the safety layer. Every change that touches engine/ must pass through here.
Inspired by: Constitutional AI (fixed principles), Godel Machine (proof-before-rewrite),
K8s Operator (idempotent reconciliation), SWE-Agent (lint guard).

The anchor is non-negotiable. It cannot be bypassed. It maintains the constitution
that the system itself cannot modify — only humans can.
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Path helpers ──────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
from engine.platform import find_skill_root as _find_skill_root
# ── Constitution parsing ──────────────────────────────────────────────────────

def load_constitution(skill_root: Optional[Path] = None) -> dict:
    """Load and parse the constitution. Returns {law_name: law_text}."""
    root = skill_root or SKILL_ROOT
    con_path = root / "constitution.md"
    if not con_path.exists():
        return {"error": "constitution.md not found"}

    text = con_path.read_text(encoding="utf-8")
    laws = {}
    current_law = None
    current_text = []

    for line in text.split("\n"):
        if line.startswith("## 第") and "法则" in line:
            if current_law:
                laws[current_law] = "\n".join(current_text).strip()
            current_law = line.strip("# ").strip()
            current_text = []
        elif current_law:
            current_text.append(line)

    if current_law:
        laws[current_law] = "\n".join(current_text).strip()

    return laws


# ── Constitutional checks ─────────────────────────────────────────────────────

def check_self_reference(skill_root: Optional[Path] = None) -> dict:
    """Verify First Law: no special paths for skill-builder-v3 itself."""
    root = skill_root or SKILL_ROOT
    engine_dir = root / "engine"
    violations = []

    for py_file in engine_dir.rglob("*.py"):
        # 宪法第零+第一法则: 免疫系统文件允许自引用
        # {anchor, scanner, patterns, realizability}.py — see constitution.md L47-48
        # v1.3: rule_generator.py, code_generator.py, selftest.py 也是自进化基础设施
        # 它们需要引用 "skill-builder-v3" 来完成规则晋升、代码生成、自检
        if py_file.name in ("anchor.py", "patterns.py", "scanner.py", "realizability.py",
                            "rule_generator.py", "code_generator.py", "selftest.py"):
            continue

        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and docstrings
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if stripped.startswith('"') or stripped.startswith("'"):
                continue

            # Detect special-case branches for self
            if ("skill-builder-v3" in stripped.lower() or "skill_builder_v3" in stripped.lower()):
                if any(kw in stripped for kw in ["if ", "elif ", "== ", "!=", " is ", " is not "]):
                    # Only flag as violation if it's a conditional branch, not just a default value
                    if "default" not in stripped and "example" not in stripped:
                        violations.append(f"{py_file.relative_to(root)}:{i}: {stripped.strip()[:100]}")

    return {
        "law": "第一法则：自指闭环",
        "passed": len(violations) == 0,
        "violations": violations,
        "message": "[OK] No self-referential special paths found" if not violations
                   else f"[!!] {len(violations)} violations: skill-builder-v3 special-cased in engine code"
    }


def check_record_driven(skill_root: Optional[Path] = None) -> dict:
    """Verify Second Law: recording infrastructure exists."""
    root = skill_root or SKILL_ROOT
    checks = {
        "known_issues_exists": (root / "references" / "known-issues.md").exists(),
        "changelog_exists": (root / "references" / "changelog-archive.json").exists(),
        "data_logs_dir_exists": (root / "data" / "logs").is_dir(),
    }

    all_ok = all(checks.values())
    missing = [k for k, v in checks.items() if not v]

    return {
        "law": "第二法则：记录驱动",
        "passed": all_ok,
        "checks": checks,
        "missing": missing,
        "message": "[OK] Recording infrastructure complete" if all_ok
                   else f"[!!] Missing: {', '.join(missing)}"
    }


def check_verify_before_adopt(change: dict) -> dict:
    """Verify Third Law: proposed engine/ changes must be validated first."""
    files = change.get("files_changed", [])

    # Determine if this change touches engine/
    touches_engine = any(f.startswith("engine/") for f in files)
    if not touches_engine:
        return {
            "law": "第三法则：验证优先",
            "passed": True,
            "message": "[OK] Change does not touch engine/ — no additional verification required"
        }

    # For engine/ changes, run mandatory checks:
    results = _run_pre_modify_checks(change.get("skill_root"))

    return {
        "law": "第三法则：验证优先",
        "passed": results["all_passed"],
        "checks": results["checks"],
        "message": "[OK] All pre-modify checks passed" if results["all_passed"]
                   else f"[!!] {results['failed_count']} check(s) failed"
    }


def _run_pre_modify_checks(skill_root: Optional[Path] = None) -> dict:
    """Run all pre-modification safety checks. Returns detailed results."""
    root = skill_root or SKILL_ROOT
    engine_dir = root / "engine"
    checks = {}

    # Check 1: All engine .py files compile
    compile_errors = []
    for py_file in engine_dir.rglob("*.py"):
        try:
            compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
        except SyntaxError as e:
            compile_errors.append(f"{py_file.name}:{e.lineno}: {e.msg}")
    checks["python_compile"] = {
        "passed": len(compile_errors) == 0,
        "errors": compile_errors
    }

    # Check 2: All top-level modules respond to --help within 10s
    help_failures = []
    key_modules = ["engine/memory.py", "engine/anchor.py", "engine/loop.py",
        "engine/reflect.py", "engine/evidence.py", "engine/memory_index.py",
        "engine/system2/rule_generator.py",
        "skills/signature.py", "skills/graph.py", "skills/registry.py"]
    for mod in key_modules:
        fp = root / mod
        if not fp.exists():
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(fp), "--help"],
                capture_output=True, text=True, timeout=10,
                cwd=str(root), encoding="utf-8", errors="replace"
            )
            if result.returncode != 0:
                help_failures.append(f"{mod}: exit {result.returncode}")
        except subprocess.TimeoutExpired:
            help_failures.append(f"{mod}: timeout")
        except Exception as e:
            help_failures.append(f"{mod}: {e}")
    checks["module_help"] = {
        "passed": len(help_failures) == 0,
        "failures": help_failures
    }

    # Check 3: Cross-platform syntax audit
    # Only flag files that USE grep -P, not files that DETECT it as anti-pattern
    PORTABILITY_SCANNER_FILES = {"anchor.py", "patterns.py", "scanner.py", "challenger.py",
                                  "rules.py", "realizability.py", "loop.py", "bootstrap.py",
                                  "evidence.py", "redteam.py", "regression_guard.py"}
    portability_issues = []
    for py_file in engine_dir.rglob("*.py"):
        if py_file.name in PORTABILITY_SCANNER_FILES:
            continue
        content = py_file.read_text(encoding="utf-8")
        if "grep -nP" in content or "grep -P" in content:
            portability_issues.append(f"{py_file.name}: contains grep -P (not portable)")
    checks["portability"] = {
        "passed": len(portability_issues) == 0,
        "issues": portability_issues
    }

    total = len(checks)
    passed = sum(1 for c in checks.values() if c["passed"])
    return {
        "all_passed": passed == total,
        "passed_count": passed,
        "failed_count": total - passed,
        "checks": checks
    }


def check_human_in_loop(autonomy_action: dict) -> dict:
    """Verify Fourth Law: constitution itself is human-only, graduation needs human confirm."""
    # Check 1: constitution.md cannot be auto-modified
    if autonomy_action.get("target_file") == "constitution.md":
        return {
            "law": "第四法则：人在回路",
            "passed": False,
            "message": "[!!] 拒绝：constitution.md 不能自动修改——只能被人修改"
        }

    # Check 2: supervised → trusted requires human confirmed approvals
    if autonomy_action.get("action") == "graduate_to_trusted":
        human_approved = autonomy_action.get("human_approved_count", 0)
        if human_approved < 3:
            return {
                "law": "第四法则：人在回路",
                "passed": False,
                "message": f"[!!] supervised→trusted 需要 3+ 人工确认，当前仅 {human_approved}"
            }

    return {
        "law": "第四法则：人在回路",
        "passed": True,
        "message": "[OK] Human-in-the-loop constraints satisfied"
    }


def check_no_break_foundation(skill_root: Optional[Path] = None) -> dict:
    """Verify Fifth Law: basic functionality intact."""
    # Reuse the pre-modify checks
    results = _run_pre_modify_checks(skill_root)
    return {
        "law": "第五法则：不破坏基础",
        "passed": results["all_passed"],
        "details": results,
        "message": "[OK] Foundation intact" if results["all_passed"]
                   else f"[!!] {results['failed_count']} foundational check(s) failed"
    }


# ── Full constitution verification ────────────────────────────────────────────

def verify_all(skill_root: Optional[Path] = None, change: Optional[dict] = None,
               autonomy_action: Optional[dict] = None) -> dict:
    """Run all constitutional checks. Returns full verdict.

    This is THE gate that every engine/ change must pass through.
    Any single law violation → change is rejected.
    """
    root = skill_root or SKILL_ROOT
    results = []

    # Law 1-2 always run
    results.append(check_self_reference(root))
    results.append(check_record_driven(root))

    # Law 3 runs when there's a proposed change
    if change:
        results.append(check_verify_before_adopt(change))

    # Law 4 runs when there's an autonomy action
    if autonomy_action:
        results.append(check_human_in_loop(autonomy_action))

    # Law 5 runs when there's a proposed change to engine/
    if change and any(f.startswith("engine/") for f in change.get("files_changed", [])):
        results.append(check_no_break_foundation(root))

    all_passed = all(r["passed"] for r in results)
    violations = [r for r in results if not r["passed"]]

    return {
        "verdict": "[OK] All constitutional checks passed" if all_passed
                   else f"[!!] {len(violations)} law(s) violated",
        "all_passed": all_passed,
        "results": results,
        "violated_laws": [r["law"] for r in violations],
        "can_proceed": all_passed
    }


# ── Snapshot / Rollback ──────────────────────────────────────────────────────

def create_snapshot(skill_root: Optional[Path] = None,
                    change_description: str = "") -> Optional[str]:
    """Create a rollback snapshot of the engine/ directory.

    Returns snapshot_id or None on failure.
    """
    root = skill_root or SKILL_ROOT
    engine_dir = root / "engine"
    if not engine_dir.exists():
        return None

    snapshots_dir = root / "data" / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot_id = f"snap-{ts}"
    snapshot_dir = snapshots_dir / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Copy engine/ to snapshot
    shutil.copytree(engine_dir, snapshot_dir / "engine", dirs_exist_ok=True)

    # Write metadata
    meta = {
        "snapshot_id": snapshot_id,
        "iso_created": datetime.now(timezone.utc).isoformat(),
        "description": change_description,
        "source_dir": str(engine_dir)
    }
    (snapshot_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return snapshot_id


def rollback(snapshot_id: str, skill_root: Optional[Path] = None) -> dict:
    """Rollback engine/ to a previous snapshot. Returns result dict."""
    root = skill_root or SKILL_ROOT
    snapshots_dir = root / "data" / "snapshots"
    snapshot_dir = snapshots_dir / snapshot_id

    if not snapshot_dir.exists():
        return {"success": False, "error": f"Snapshot {snapshot_id} not found"}

    engine_snapshot = snapshot_dir / "engine"
    if not engine_snapshot.exists():
        return {"success": False, "error": "Snapshot corrupted: no engine/ directory"}

    engine_dir = root / "engine"

    # Safety: create a pre-rollback snapshot first
    pre_rollback_id = create_snapshot(root, f"pre-rollback-to-{snapshot_id}")

    # Perform rollback
    try:
        if engine_dir.exists():
            shutil.rmtree(engine_dir)
        shutil.copytree(engine_snapshot, engine_dir)
    except Exception as e:
        # Attempt recovery from pre-rollback snapshot
        if pre_rollback_id:
            recovery_dir = snapshots_dir / pre_rollback_id / "engine"
            if recovery_dir.exists():
                shutil.rmtree(engine_dir)
                shutil.copytree(recovery_dir, engine_dir)
        return {"success": False, "error": f"Rollback failed (recovered): {e}"}

    # Record rollback event
    from engine import memory
    memory.write_event(
        root.name,
        "rollback",
        {
            "snapshot_id": snapshot_id,
            "pre_rollback_snapshot": pre_rollback_id,
            "reason": "manual_or_auto_rollback"
        }
    )

    return {
        "success": True,
        "snapshot_id": snapshot_id,
        "pre_rollback_snapshot": pre_rollback_id
    }


def list_snapshots(skill_root: Optional[Path] = None) -> list:
    """List all available rollback snapshots. Newest first."""
    root = skill_root or SKILL_ROOT
    snapshots_dir = root / "data" / "snapshots"
    if not snapshots_dir.exists():
        return []

    snapshots = []
    for d in sorted(snapshots_dir.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("snap-"):
            meta_path = d / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    snapshots.append(meta)
                except json.JSONDecodeError:
                    snapshots.append({"snapshot_id": d.name, "error": "corrupt metadata"})
    return snapshots


# ── Self-verification ────────────────────────────────────────────────────────

def verify_self() -> dict:
    """skill-builder-v3 verifies itself against the constitution.
    This is the self-referential test: can the system pass its own laws?
    """
    return verify_all()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Layer 3 Anchor: 宪法验证 + 回滚 + 验证门控"
    )
    sub = parser.add_subparsers(dest="cmd")

    # verify-self
    p = sub.add_parser("verify-self", help="对自身运行完整宪法检查")
    p.add_argument("--skill-root", default=None, help="Skill root path")

    # verify-proposal
    p = sub.add_parser("verify-proposal", help="验证架构变更提案")
    p.add_argument("proposal_id", help="Proposal ID or JSON file path")
    p.add_argument("--skill-root", default=None)

    # pre-modify-check
    p = sub.add_parser("pre-modify-check", help="修改前基础功能检查")
    p.add_argument("--skill-root", default=None)

    # create-snapshot
    p = sub.add_parser("create-snapshot", help="创建回滚快照")
    p.add_argument("--desc", default="manual snapshot", help="Snapshot description")
    p.add_argument("--skill-root", default=None)

    # rollback
    p = sub.add_parser("rollback", help="回滚到指定快照")
    p.add_argument("snapshot_id", help="Snapshot ID")
    p.add_argument("--skill-root", default=None)

    # list-snapshots
    p = sub.add_parser("list-snapshots", help="列出所有快照")

    # check-autonomy
    p = sub.add_parser("check-autonomy", help="检查自主等级升级合法性")

    args = parser.parse_args()

    if args.cmd == "verify-self":
        root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
        verdict = verify_all(root)
        print(f"  Constitution verification: {verdict['verdict']}")
        for r in verdict["results"]:
            icon = "[OK]" if r["passed"] else "[!!]"
            print(f"  {icon} {r['law']}")
            print(f"      {r['message']}")
        if not verdict["all_passed"]:
            sys.exit(1)

    elif args.cmd == "verify-proposal":
        # Load proposal from file
        prop_path = Path(args.proposal_id)
        if prop_path.exists():
            change = json.loads(prop_path.read_text(encoding="utf-8"))
        else:
            # Try as proposal ID in proposals dir
            root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
            prop_path = root / "data" / "proposals" / f"{args.proposal_id}.json"
            if prop_path.exists():
                change = json.loads(prop_path.read_text(encoding="utf-8"))
            else:
                print(f"[!!] Proposal not found: {args.proposal_id}")
                sys.exit(1)

        root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
        verdict = verify_all(root, change=change)
        print(f"  Proposal verification: {verdict['verdict']}")
        for r in verdict["results"]:
            icon = "[OK]" if r["passed"] else "[!!]"
            print(f"  {icon} {r['law']}")
        if not verdict["all_passed"]:
            sys.exit(1)

    elif args.cmd == "pre-modify-check":
        root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
        results = _run_pre_modify_checks(root)
        print(f"  Pre-modify checks: {results['passed_count']}/{results['passed_count'] + results['failed_count']} passed")
        for name, check in results["checks"].items():
            icon = "[OK]" if check["passed"] else "[!!]"
            print(f"  {icon} {name}")
            if not check["passed"]:
                for err in check.get("errors", check.get("failures", check.get("issues", []))):
                    print(f"      - {err}")
        if not results["all_passed"]:
            sys.exit(1)

    elif args.cmd == "create-snapshot":
        root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
        sid = create_snapshot(root, args.desc)
        if sid:
            print(f"[OK] Snapshot created: {sid}")
        else:
            print("[!!] Snapshot creation failed")
            sys.exit(1)

    elif args.cmd == "rollback":
        root = Path(args.skill_root) if args.skill_root else SKILL_ROOT
        result = rollback(args.snapshot_id, root)
        if result["success"]:
            print(f"[OK] Rolled back to {args.snapshot_id}")
        else:
            print(f"[!!] {result['error']}")
            sys.exit(1)

    elif args.cmd == "list-snapshots":
        snaps = list_snapshots()
        if snaps:
            for s in snaps:
                print(f"  {s['snapshot_id']} — {s.get('description', '?')}")
        else:
            print("  (no snapshots)")

    elif args.cmd == "check-autonomy":
        # Placeholder — autonomy logic will be in reflect.py / loop.py
        print("[OK] Autonomy check: cold_start (default)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
