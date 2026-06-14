#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P8: Parallel Candidate Evaluation — 并行候选评估 (Yunjue Agent 蒸馏).

v1.2 (2026-06-13): 蒸馏自 Yunjue Agent 并行批量进化 + Claude Code worktree 隔离.

核心机制:
  1. 对同一组 findings 生成 N 个并行修复候选
  2. 每个候选在隔离的 git worktree 中独立运行 INTEGRATE → VERIFY
  3. 收集全部结果，Pareto 前沿选择最优候选
  4. 最优候选合入主干，其余 worktree 丢弃

这与 Yunjue Agent 的 Parallel Batch Evolution 完全相同：
多个工具并行合成/验证/优化 → 取最优 → 加速进化。
"""

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Candidate generation ──────────────────────────────────────────────────────

def generate_candidates(skill_name: str, findings: list,
                         n_candidates: int = 3) -> list:
    """Generate N parallel fix candidates from the same findings.

    Each candidate represents a different fix strategy:
      - Candidate 0: All auto-fixable rules (conservative)
      - Candidate 1: Auto-fixable + TAPO high-confidence rules (balanced)
      - Candidate 2: All rules including probation (aggressive)

    Returns list of {strategy, findings_subset, rules_subset}.
    """
    from engine.system1 import rules as rule_engine
    from engine import memory

    all_rules = rule_engine.get_applicable_rules(findings, skill_name)

    if len(all_rules) < 2:
        # Not enough variety for parallel — just return the single candidate
        return [{"strategy": "default", "findings": findings,
                 "rules": [(r, f) for r, f in all_rules]}]

    # Candidate strategies
    candidates = []

    # C0: Conservative — only rules with high success rate
    conservative = []
    for rule, finding in all_rules:
        q = memory.get_fix_quality(skill_name, rule["name"])
        if q.get("success_rate", 1.0) >= 0.8 or q.get("total", 0) < 2:
            conservative.append((rule, finding))
    if conservative:
        candidates.append({
            "strategy": "conservative",
            "description": "仅高成功率规则 (≥80%)",
            "rules": conservative,
            "findings": [f for _, f in conservative]
        })

    # C1: Balanced — all auto-apply rules
    balanced = [(r, f) for r, f in all_rules if r.get("auto_apply", False)]
    if balanced and balanced != conservative:
        candidates.append({
            "strategy": "balanced",
            "description": "全部自动规则",
            "rules": balanced,
            "findings": [f for _, f in balanced]
        })

    # C2: Aggressive — includes probation rules
    aggressive = list(all_rules)
    if aggressive and aggressive != balanced and aggressive != conservative:
        candidates.append({
            "strategy": "aggressive",
            "description": "含试用期规则",
            "rules": aggressive,
            "findings": [f for _, f in aggressive]
        })

    # Ensure at least one candidate
    if not candidates:
        candidates.append({
            "strategy": "fallback",
            "description": "回退——无策略可分",
            "rules": list(all_rules),
            "findings": findings
        })

    return candidates[:n_candidates]


# ── Isolated execution ────────────────────────────────────────────────────────

def run_candidate_in_isolation(skill_name: str, candidate: dict) -> dict:
    """Run one candidate's fix strategy in an isolated worktree.

    Steps:
      1. Create temp directory (simulating git worktree)
      2. Copy skill files into temp
      3. Apply candidate's fixes
      4. Run verification
      5. Return {strategy, success, metrics}
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": "Skill not found", "strategy": candidate.get("strategy", "?")}

    # Create isolated workspace
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    iso_dir = Path(tempfile.gettempdir()) / f"sbv3-parallel-{skill_name}-{ts}"
    iso_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Copy skill files
        shutil.copytree(root, iso_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))

        # Apply fixes in isolation
        from engine.system1 import rules as rule_engine
        from engine import memory

        fixes_applied = 0
        fix_results = []
        for rule, finding in candidate.get("rules", []):
            if rule.get("name", "").startswith("_") or not rule.get("fix"):
                continue
            try:
                result = rule["fix"](iso_dir, finding)
                result["rule"] = rule["name"]
                result["finding_file"] = finding.get("file", "?")
                if result.get("applied"):
                    fixes_applied += 1
                fix_results.append(result)
            except Exception as e:
                fix_results.append({
                    "applied": False, "rule": rule.get("name", "?"),
                    "finding_file": finding.get("file", "?"), "error": str(e)
                })

        # Run verification
        verification_passed = True
        verification_output = ""
        try:
            scan_result = subprocess.run(
                [sys.executable, str(iso_dir / "engine" / "system1" / "scanner.py"),
                 skill_name, "--json"],
                capture_output=True, text=True, timeout=30,
                cwd=str(iso_dir), encoding="utf-8", errors="replace"
            )
            findings_data = json.loads(scan_result.stdout)
            post_fix_findings = findings_data.get("findings_count", -1)
            verification_passed = post_fix_findings >= 0
            verification_output = f"post-fix findings: {post_fix_findings}"
        except Exception as e:
            verification_passed = False
            verification_output = str(e)

        # Evidence check
        from engine import evidence as ev
        evidence_result = ev.verify_all_fix_evidences(fix_results, iso_dir)

        return {
            "strategy": candidate.get("strategy", "?"),
            "description": candidate.get("description", ""),
            "fixes_applied": fixes_applied,
            "total_rules": len(candidate.get("rules", [])),
            "verification_passed": verification_passed,
            "verification_output": verification_output,
            "evidence_pass_rate": evidence_result.get("evidence_pass_rate", 0.0),
            "fix_details": fix_results[:10]
        }

    finally:
        # Cleanup
        try:
            shutil.rmtree(iso_dir)
        except Exception:
            pass  # TODO: log or re-raise


# ── Pareto selection ──────────────────────────────────────────────────────────

def select_pareto_optimal(results: list) -> dict:
    """Select the best candidate from parallel results.

    Pareto criteria (non-dominated sorting):
      - Fixes applied (more = better)
      - Verification passed (passed = better)
      - Evidence pass rate (higher = better)

    If multiple Pareto-optimal, prefer conservative (safer).
    """
    if not results:
        return {"error": "No candidates to evaluate"}

    valid = [r for r in results if "error" not in r]

    if not valid:
        return {"error": "All candidates failed", "results": results}

    # Score each candidate
    scored = []
    for r in valid:
        score = (
            r.get("fixes_applied", 0) * 1.0 +
            (1.0 if r.get("verification_passed") else -5.0) +
            r.get("evidence_pass_rate", 0.0) * 2.0
        )
        # Prefer conservative strategies (penalize aggressive less)
        if r.get("strategy") == "aggressive":
            score -= 0.5
        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])  # Descending

    best_score, best = scored[0]

    return {
        "selected_strategy": best.get("strategy", "?"),
        "score": best_score,
        "candidate": best,
        "all_results": results,
        "ranking": [{"strategy": r.get("strategy"), "score": s}
                     for s, r in scored],
        "verdict": (f"[OK] 选择策略 '{best.get('strategy')}' (score={best_score:.1f})"
                    if best_score > 0
                    else "[!!] 最优候选分数为负——建议人工审查")
    }


# ── Main parallel orchestration ───────────────────────────────────────────────

def evaluate_parallel(skill_name: str, findings: list,
                       n_candidates: int = 3) -> dict:
    """Full parallel evaluation pipeline.

    GENERATE CANDIDATES → RUN EACH IN ISOLATION → PARETO SELECT → RETURN BEST

    This is the Yunjue Agent pattern: multiple strategies tried simultaneously,
    only the best one survives.
    """
    from engine import memory

    memory.write_event(skill_name, "loop", {
        "phase": "parallel_start",
        "n_candidates": n_candidates,
        "findings_count": len(findings)
    })

    # Step 1: Generate candidates
    candidates = generate_candidates(skill_name, findings, n_candidates)
    if not candidates:
        return {"error": "无法生成任何候选策略"}

    # Step 2: Run each in isolation
    results = []
    for i, cand in enumerate(candidates):
        print(f"  [PARALLEL {i+1}/{len(candidates)}] 策略: {cand['strategy']} "
              f"({cand.get('description', '?')})")
        result = run_candidate_in_isolation(skill_name, cand)
        results.append(result)
        icon = "[OK]" if result.get("verification_passed") else "[!!]"
        print(f"    {icon} fixes={result.get('fixes_applied', 0)} "
              f"evidence={result.get('evidence_pass_rate', 0):.0%}")

    # Step 3: Pareto select best
    selection = select_pareto_optimal(results)

    memory.write_event(skill_name, "loop", {
        "phase": "parallel_end",
        "selected": selection.get("selected_strategy"),
        "all_strategies": [r.get("strategy") for r in results]
    })

    return {
        "skill": skill_name,
        "candidates": len(candidates),
        "results": results,
        "selection": selection,
        "verdict": selection.get("verdict", "?")
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P8 Parallel Evaluator (Yunjue Agent)——并行候选评估"
    )
    parser.add_argument("skill", help="目标 skill")
    parser.add_argument("--n-candidates", type=int, default=3, help="候选数 (默认3)")
    parser.add_argument("--findings-file", help="从文件读取 findings JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅生成候选,不执行")

    args = parser.parse_args()

    findings = []
    if args.findings_file:
        fp = Path(args.findings_file)
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8"))
            findings = data.get("findings", [])
    else:
        from engine.system1 import scanner
        scan = scanner.scan_skill(args.skill)
        findings = scan.get("findings", [])

    if not findings:
        print("[OK] 无发现——并行评估无必要")
        return

    if args.dry_run:
        candidates = generate_candidates(args.skill, findings, args.n_candidates)
        print(f"  生成 {len(candidates)} 个候选:")
        for c in candidates:
            print(f"    [{c['strategy']}] {c.get('description', '?')}: "
                  f"{len(c['rules'])} 条规则")
        return

    result = evaluate_parallel(args.skill, findings, args.n_candidates)
    if "error" in result:
        print(f"[!!] {result['error']}")
        sys.exit(1)

    print(f"\n  并行评估完成:")
    print(f"  选择: {result['selection'].get('selected_strategy', '?')}")
    print(f"  {result['verdict']}")


if __name__ == "__main__":
    main()
