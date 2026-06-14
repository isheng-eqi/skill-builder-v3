#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""S1: Trace Distiller — Socratic-SWE 轨迹→技能蒸馏 (Socratic-SWE, 2026).

v1.2: 蒸馏自 Socratic-SWE (arXiv 2606.07412) 三项核心机制:
  A. GDPO 三维奖励分解 — 全过(50%) + 部分修复率(30%) + 未退化(20%)
  B. 四阶段验证门 — 格式 → 扎根 → 执行稳定性 → 语义有效性
  C. 轨迹→技能蒸馏 — 从完整求解轨迹(含失败)提取结构化技能

关键洞察 (Socratic-SWE):
  "Traces from solving attempts — especially failures — are distilled into
   structured Agent Skills. The agent uses these skills to generate targeted
   new tasks that address its specific weaknesses."

当前 v1.2 的问题:
  probation 是机械的 5 次试用 + 布尔 pass/fail。没有部分修复的概念。
  一个修复改了 3 个文件，2 个对 1 个错 → 当前标记为 False，GDPO 会标记为 0.7。

改正:
  - 修复不再二元判断 → 三维评分
  - probation 不是固定 5 次 → 基于 GDPO 累积分数自适应决定
  - 每次失败携带"文本梯度"(与 S2: TextGrad 联动)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# A. GDPO 三维奖励分解
# ═══════════════════════════════════════════════════════════════════════════

def score_fix_gdpo(skill_name: str, fix_report: dict,
                    pre_scan_result: dict,
                    post_scan_result: dict) -> dict:
    """Socratic-SWE GDPO: decompose fix quality into 3 dimensions.

    NOT binary pass/fail. Instead:
      D1 (Full Pass):   Are ALL pre-fix findings resolved?                     weight 0.5
      D2 (Partial Fix): What fraction of individual findings were fixed?        weight 0.3
      D3 (No Regression): Did this fix introduce NEW findings?                  weight 0.2

    Returns {gdpo_overall, d1/d2/d3 scores, verdict, is_good_enough}.
    """
    # D1: Full Pass — all pre-fix findings gone
    pre_total = pre_scan_result.get("findings_count", 1)
    post_total = post_scan_result.get("findings_count", 0)
    d1_score = 1.0 if post_total == 0 else 0.0

    # D2: Partial Fix Rate — what fraction were fixed
    applied = fix_report.get("applied", 0)
    total = max(1, fix_report.get("total_fixes", applied))
    d2_score = applied / total

    # D3: Regression Prevention — did we introduce new findings?
    # Compare pre-fix pattern types vs post-fix pattern types
    pre_patterns = set(f.get("pattern", "") for f in pre_scan_result.get("findings", []))
    post_patterns = set(f.get("pattern", "") for f in post_scan_result.get("findings", []))
    new_patterns = post_patterns - pre_patterns
    # Penalize if more than 1 new pattern type appeared
    d3_score = max(0.0, 1.0 - len(new_patterns) * 0.3)

    # Weighted composite
    gdpo_overall = d1_score * 0.5 + d2_score * 0.3 + d3_score * 0.2

    verdict = ("[PASS]" if gdpo_overall >= 0.7
               else "[PARTIAL]" if gdpo_overall >= 0.4
               else "[FAIL]")

    return {
        "gdpo_overall": round(gdpo_overall, 3),
        "d1_full_pass": d1_score,
        "d2_partial_fix_rate": round(d2_score, 3),
        "d3_no_regression": round(d3_score, 3),
        "new_patterns_introduced": list(new_patterns),
        "verdict": verdict,
        "is_good_enough": gdpo_overall >= 0.7,
        "is_partial": 0.4 <= gdpo_overall < 0.7,
        "is_failure": gdpo_overall < 0.4
    }


def adaptive_probation_threshold(gdpo_history: list) -> int:
    """Adaptive probation: not fixed 5 trials.

    If agent's recent GDPO scores are consistently high (avg > 0.8),
    probation can end early (3 trials). If inconsistent (avg < 0.6),
    probation extends (8 trials).

    This implements the Socratic-SWE insight that "the agent's capability
    should determine how much evidence is needed before trusting a rule."
    """
    if len(gdpo_history) < 3:
        return 5  # Default

    recent = [g.get("gdpo_overall", 0) for g in gdpo_history[-5:]]
    avg = sum(recent) / len(recent)

    if avg >= 0.85:
        return 3   # Trust earned — fast track
    elif avg >= 0.7:
        return 5   # Standard
    elif avg >= 0.5:
        return 8   # Needs more evidence
    else:
        return 12  # Very unreliable — extended probation


# ═══════════════════════════════════════════════════════════════════════════
# B. 四阶段验证门 (Socratic-SWE Verifier Gate)
# ═══════════════════════════════════════════════════════════════════════════

def verify_fix_four_stage(fix_report: dict, skill_root: Path,
                           evidence_span: Optional[dict] = None) -> dict:
    """Socratic-SWE 四阶段验证: 每个候选修复必须通过四道闸门。

    G1 (Format):    修复后的代码语法正确?
    G2 (Grounding): 修复引用的文件/函数确实存在? (无幻觉)
    G3 (Stability): 修复后的代码多次运行结果一致? (非flaky)
    G4 (Semantic):  修复真正解决了问题? 不是治标?

    任何一门失败 → 修复被拒绝。
    """
    import subprocess

    gates = {}

    # G1: Format — compile check
    files_changed = fix_report.get("files_changed", [])
    format_failures = []
    for f in files_changed:
        fp = skill_root / f
        if fp.exists() and fp.suffix == ".py":
            try:
                compile(fp.read_text(encoding="utf-8"), str(fp), "exec")
            except SyntaxError as e:
                format_failures.append(f"{f}:{e.lineno}: {e.msg}")
    gates["G1_format"] = {
        "passed": len(format_failures) == 0,
        "failures": format_failures,
        "description": "修复后代码语法正确"
    }

    # G2: Grounding — files referenced actually exist
    grounding_failures = []
    for f in files_changed:
        if not (skill_root / f).exists():
            grounding_failures.append(f)
    # Also check evidence span references
    if evidence_span:
        ev_file = evidence_span.get("source_file", "")
        if ev_file and not (skill_root / ev_file).exists():
            grounding_failures.append(f"evidence references missing file: {ev_file}")
    # G2.1: Content grounding — scan file contents for path references
    # Detect hallucinated references like "fixed in nonexistent/file.py" in comments
    import re
    _PATH_PATTERN = re.compile(r'["\']?([\w./-]+\.(?:py|js|ts|json|yaml|yml|md|txt|sh|bat))["\']?')
    for f in files_changed:
        fp = skill_root / f
        if not fp.exists() or fp.suffix != ".py":
            continue
        try:
            content = fp.read_text(encoding="utf-8")
            for match in _PATH_PATTERN.finditer(content):
                ref_path = match.group(1)
                # Skip standard library imports and well-known paths
                if any(ref_path.startswith(p) for p in ("sys.", "os.", "re.", "json.", "pathlib.")):
                    continue
                # Check if this looks like a file path (contains / or .) and doesn't exist
                if "/" in ref_path and not (skill_root / ref_path).exists():
                    # Try relative to skill root
                    if not (skill_root / ref_path.split("/")[-1]).exists():
                        grounding_failures.append(
                            f"{f} references nonexistent file: {ref_path}")
        except (UnicodeDecodeError, OSError):
            continue
    gates["G2_grounding"] = {
        "passed": len(grounding_failures) == 0,
        "failures": grounding_failures,
        "description": "引用的文件确实存在(无幻觉)"
    }

    # G3: Stability — run twice, compare outputs
    stability_ok = True
    stability_note = ""
    # Don't use compile commands for stability — they're always deterministic.
    # Only use evidence commands that exercise runtime behavior (not py_compile/mypy).
    ev_type = evidence_span.get("evidence_type", "") if evidence_span else ""
    ev_cmd = evidence_span.get("command", "") if evidence_span else ""
    is_static_cmd = any(kw in ev_cmd for kw in ("py_compile", "compile(", "mypy", "flake8", "pylint"))
    if files_changed and evidence_span and ev_cmd and not is_static_cmd:
        cmd = ev_cmd
        try:
            r1 = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=15, cwd=str(skill_root),
                                encoding="utf-8", errors="replace")
            r2 = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=15, cwd=str(skill_root),
                                encoding="utf-8", errors="replace")
            stability_ok = (r1.returncode == r2.returncode and
                           r1.stdout == r2.stdout and r1.stderr == r2.stderr)
        except Exception:
            stability_ok = False
    # G3.1: Active stability — run .py files directly twice and compare
    elif files_changed:
        stability_ok = False  # Will be set True if at least one file passes
        for f in files_changed:
            fp = skill_root / f
            if not fp.exists() or fp.suffix != ".py":
                continue
            try:
                r1 = subprocess.run([sys.executable, str(fp)],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace")
                r2 = subprocess.run([sys.executable, str(fp)],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace")
                stable = (r1.returncode == r2.returncode and
                          r1.stdout == r2.stdout and r1.stderr == r2.stderr)
                if stable:
                    stability_ok = True
                    break  # At least one file is stable
            except Exception:
                continue
        stability_note = "主动运行验证(无evidence命令)"
    gates["G3_stability"] = {
        "passed": stability_ok,
        "description": "多次运行输出一致(非flaky)",
        "note": stability_note or ("跳过——无证据命令" if not files_changed else "")
    }

    # G4: Semantic validity — did the fix actually address the root problem?
    # Check: post-fix scan should have fewer critical findings than pre-fix
    semantic_ok = True
    semantic_issues = []
    if fix_report.get("_pre_scan") and fix_report.get("_post_scan"):
        pre_crit = fix_report["_pre_scan"].get("by_severity", {}).get("critical", 0)
        post_crit = fix_report["_post_scan"].get("by_severity", {}).get("critical", 0)
        pre_warn = fix_report["_pre_scan"].get("by_severity", {}).get("warning", 0)
        post_warn = fix_report["_post_scan"].get("by_severity", {}).get("warning", 0)
        pre_info = fix_report["_pre_scan"].get("by_severity", {}).get("info", 0)
        post_info = fix_report["_post_scan"].get("by_severity", {}).get("info", 0)

        # G4.1: Severity masking check — critical dropped but warning increased
        crit_dropped = pre_crit - post_crit
        warn_increased = post_warn - pre_warn
        info_increased = post_info - pre_info
        if crit_dropped > 0 and (warn_increased >= crit_dropped or info_increased >= crit_dropped):
            semantic_issues.append(
                f"严重度伪装嫌疑: critical {pre_crit}→{post_crit} ({-crit_dropped}), "
                f"但 warning {pre_warn}→{post_warn} (+{warn_increased}), "
                f"info {pre_info}→{post_info} (+{info_increased})")

        # G4.2: Noise flooding check — file size ballooned while findings barely changed
        total_dropped = (pre_crit + pre_warn + pre_info) - (post_crit + post_warn + post_info)
        for f in files_changed:
            fp = skill_root / f
            if not fp.exists():
                continue
            try:
                lines = len(fp.read_text(encoding="utf-8").split("\n"))
            except (UnicodeDecodeError, OSError):
                continue
            # If file is huge (>200 lines) and findings barely changed, suspect noise
            if lines > 200 and total_dropped <= 2:
                semantic_issues.append(
                    f"噪声淹没嫌疑: {f} 膨胀至 {lines} 行, "
                    f"但 findings 仅减少 {total_dropped}")

        semantic_ok = post_crit <= pre_crit and len(semantic_issues) == 0
    gates["G4_semantic"] = {
        "passed": semantic_ok,
        "description": "修复真正解决问题(非治标)",
        "issues": semantic_issues,
        "note": "; ".join(semantic_issues) if semantic_issues else
                (f"critical: {fix_report.get('_pre_scan', {}).get('by_severity', {}).get('critical', '?')}"
                 f"→{fix_report.get('_post_scan', {}).get('by_severity', {}).get('critical', '?')}"
                 if fix_report.get("_pre_scan") and fix_report.get("_post_scan") else "")
    }

    passed = sum(1 for g in gates.values() if g["passed"])
    total = len(gates)

    return {
        "all_passed": passed == total,
        "passed_count": passed,
        "total": total,
        "gates": gates,
        "verdict": f"[OK] {passed}/{total} 通过" if passed == total
                   else f"[!!] {passed}/{total} 通过——被拒绝:{[k for k,v in gates.items() if not v['passed']]}"
    }


# ═══════════════════════════════════════════════════════════════════════════
# C. 轨迹→技能蒸馏
# ═══════════════════════════════════════════════════════════════════════════

def collect_solving_traces(skill_name: str, lookback: int = 30) -> list:
    """Collect all execution traces from recent events.

    Each trace = a fix attempt with full context:
      {rule, files, evidence, gdpo_score, success/failure, what_was_tried}

    Failure traces are MORE valuable than success traces (Socratic-SWE finding).
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=lookback * 3)
    traces = [e for e in events if e["event_type"] in ("execution_trace", "evidence_span")]

    solving_traces = []
    for t in traces:
        data = t.get("data", {})
        trace = {
            "event_id": t.get("event_id", "?"),
            "iso": t.get("iso_timestamp", ""),
            "rule": data.get("fix_rule", data.get("rule", "?")),
            "file": data.get("fix_file", data.get("file", "?")),
            "success": data.get("success", data.get("verdict") == "evidence_consistent"),
            "evidence_type": data.get("evidence_type", "?"),
            "command": data.get("command", "?")[:200],
            "expected": data.get("expected", "")[:200],
            "actual": data.get("actual", "")[:200],
            "exit_code": data.get("exit_code", -1),
        }
        solving_traces.append(trace)

    # Label: successes vs failures (failures first — more informative)
    successes = [t for t in solving_traces if t["success"]]
    failures = [t for t in solving_traces if not t["success"]]

    return {
        "total_traces": len(solving_traces),
        "success_count": len(successes),
        "failure_count": len(failures),
        "failure_rate": len(failures) / max(1, len(solving_traces)),
        "failures": failures[:10],     # Failures first — most valuable
        "successes": successes[:10],
        "verdict": (f"[OK] 收集 {len(solving_traces)} 条求解轨迹 "
                    f"({len(failures)} 失败, {len(successes)} 成功)"
                    if solving_traces else "[!!] 无求解轨迹——需要更多修复数据")
    }


def distill_skill_from_traces(traces: list, skill_name: str) -> dict:
    """Distill structured agent skills from solving traces.

    Socratic-SWE pattern:
    Input:  traces (successes + failures)
    Output: skill {name, description, applicability_conditions, operations}

    Key finding: failures are MORE valuable than successes for skill discovery.

    This builds a "Agent Skill Registry" entry that can be used to:
      1. Guide future fix attempts
      2. Generate targeted training tasks for the agent
    """
    from engine import memory

    if not traces or not traces.get("failures"):
        return {"skills_distilled": 0, "skills": [],
                "verdict": "[--] 无失败轨迹——无需蒸馏"}

    failures = traces["failures"]
    skills = []

    # Group failures by rule type
    by_rule = {}
    for f in failures:
        rule = f.get("rule", "unknown")
        if rule not in by_rule:
            by_rule[rule] = []
        by_rule[rule].append(f)

    # Distill one skill per failure cluster
    for rule_name, failure_list in by_rule.items():
        if len(failure_list) < 2:
            continue  # Need at least 2 failures to spot a pattern

        # Find common files affected
        common_files = set()
        for f in failure_list:
            ffile = f.get("file", "")
            if ffile and ffile != "?":
                common_files.add(ffile)

        # Find common evidence patterns
        evidence_types = set(f.get("evidence_type", "?") for f in failure_list)
        common_errors = set(
            f.get("actual", "")[:100] for f in failure_list
            if "error" in str(f.get("actual", "")).lower() or
               "fail" in str(f.get("actual", "")).lower()
        )

        skill = {
            "skill_name": f"traced:{rule_name}",
            "description": (f"从 {len(failure_list)} 次失败修复中蒸馏: "
                          f"规则 '{rule_name}' 在 {common_files} 上反复失败"),
            "applicability_conditions": list(evidence_types),
            "affected_files": list(common_files),
            "common_error_patterns": list(common_errors)[:3],
            "failure_count": len(failure_list),
            "recommended_operations": [
                "1. 确认修复文件存在且语法正确 (G2 grounding)",
                "2. 独立验证器重新扫描——不接受自我验证 (P3)",
                "3. 如果证据类型为 compile_check 但实际退出非0 → 修复本身引入语法错误",
                "4. 如果证据类型为 run_test 且超时 → 修复产生死循环或阻塞"
            ],
            "iso_distilled": datetime.now(timezone.utc).isoformat(),
            "source": "socratic-swe-trace-distillation"
        }
        skills.append(skill)

        # Write insight for each distilled skill
        memory.write_insight(
            skill_name,
            f"Socratic-SWE 轨迹蒸馏: 规则 '{rule_name}' 重复失败 {len(failure_list)} 次. "
            f"公共文件: {common_files}. "
            f"公共错误模式: {common_errors}. "
            f"建议操作: {skill['recommended_operations']}",
            failure_list[:5],
            confidence=0.7 + min(len(failure_list) * 0.05, 0.25)
        )

    return {
        "skills_distilled": len(skills),
        "skills": skills,
        "total_failures_analyzed": len(failures),
        "verdict": (f"[OK] 蒸馏 {len(skills)} 个技能 "
                    f"从 {len(failures)} 次失败中" if skills
                    else f"[--] {len(failures)} 次失败未形成足够模式")
    }


# ═══════════════════════════════════════════════════════════════════════════
# D. 蒸馏主入口
# ═══════════════════════════════════════════════════════════════════════════

def run_trace_distillation(skill_name: str, lookback: int = 30,
                            auto_rule: bool = False) -> dict:
    """Full Socratic-SWE trace distillation pipeline.

    1. Collect solving traces from recent events
    2. Distill skills from failure patterns
    3. Optionally trigger rule generation for distilled skills
    4. Update Agent Skill Registry (persisted to data/)

    Returns full distillation report.
    """
    from engine import memory

    memory.write_event(skill_name, "reflect", {
        "phase": "trace_distillation_start", "lookback": lookback
    })

    # Step 1: Collect
    traces = collect_solving_traces(skill_name, lookback)
    print(f"  [S1:轨迹] 收集 {traces['total_traces']} 条求解轨迹 "
          f"({traces['failure_count']} 失败)")

    # Step 2: Distill
    skills = distill_skill_from_traces(traces, skill_name)
    if skills["skills_distilled"] > 0:
        print(f"  [S1:蒸馏] {skills['skills_distilled']} 个技能从失败中蒸馏")

        # Step 3: Auto-rule generation (Mem²Evolve trigger)
        if auto_rule:
            from engine.system2 import rule_generator
            created = 0
            for skill in skills["skills"]:
                rule = rule_generator.generate_rule_from_stubborn_pattern(
                    skill_name=skill_name,
                    pattern_name=skill["skill_name"],
                    pattern_count=skill["failure_count"],
                    affected_files=skill["affected_files"],
                    related_traces=traces.get("failures", []),
                    deliberation_text=skill["description"]
                )
                if rule:
                    created += 1
            if created:
                print(f"  [S1→S3] {created} 条候选规则从蒸馏技能生成")

    # Step 4: Persist Agent Skill Registry (generic path resolution)
    reg_root = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    registry_path = None
    for d in reg_root.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            registry_path = d / "data" / "agent_skill_registry.json"
            break
    if not registry_path:
        registry_path = SKILL_ROOT / "data" / "agent_skill_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    existing_skills = []
    if registry_path.exists():
        try:
            existing = json.loads(registry_path.read_text(encoding="utf-8"))
            existing_skills = existing.get("skills", []) if isinstance(existing, dict) else []
        except json.JSONDecodeError:
            pass  # TODO: log or re-raise
    registry = {
        "iso_updated": datetime.now(timezone.utc).isoformat(),
        "total_skills": len(existing_skills) + skills["skills_distilled"],
        "skills": existing_skills + skills["skills"],
        "stats": {
            "total_traces": traces["total_traces"],
            "failure_rate": traces["failure_rate"]
        }
    }
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    memory.write_event(skill_name, "reflect", {
        "phase": "trace_distillation_end",
        "skills_distilled": skills["skills_distilled"],
        "traces_collected": traces["total_traces"]
    })

    return {
        "skill": skill_name,
        "traces": traces,
        "skills": skills,
        "registry_path": str(registry_path),
        "verdict": (f"[OK] 蒸馏完成: {skills['skills_distilled']} 技能, "
                    f"{traces['total_traces']} 轨迹"
                    if skills["skills_distilled"] > 0
                    else f"[OK] {traces['total_traces']} 轨迹收集——无足够模式蒸馏")
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="S1 Trace Distiller (Socratic-SWE)——轨迹→技能蒸馏"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("collect", help="收集求解轨迹")
    p.add_argument("skill", help="目标skill")
    p.add_argument("--lookback", type=int, default=30)

    p = sub.add_parser("distill", help="从轨迹蒸馏技能")
    p.add_argument("skill", help="目标skill")
    p.add_argument("--lookback", type=int, default=30)
    p.add_argument("--auto-rule", action="store_true",
                   help="自动为蒸馏技能生成候选规则")

    p = sub.add_parser("gdpo", help="GDPO三维评分演示")
    p.add_argument("--applied", type=int, default=3, help="已应用修复数")
    p.add_argument("--total", type=int, default=4, help="总修复数")
    p.add_argument("--pre-findings", type=int, default=5, help="修复前发现数")
    p.add_argument("--post-findings", type=int, default=1, help="修复后发现数")
    p.add_argument("--new-patterns", type=int, default=0, help="新增模式数")

    p = sub.add_parser("registry", help="查看 Agent Skill Registry")
    p.add_argument("skill", help="目标skill")

    args = parser.parse_args()

    if args.cmd == "collect":
        result = collect_solving_traces(args.skill, args.lookback)
        print(f"  {result['verdict']}")
        for f in result.get("failures", [])[:5]:
            print(f"    [FAIL] {f['rule']}: {f.get('actual', '?')[:100]}")

    elif args.cmd == "distill":
        result = run_trace_distillation(args.skill, args.lookback, args.auto_rule)
        print(f"  {result['verdict']}")
        for s in result["skills"].get("skills", []):
            print(f"    [{s['skill_name']}] {s['failure_count']}次失败 → "
                  f"{len(s['affected_files'])}个文件")

    elif args.cmd == "gdpo":
        pre = {"findings_count": args.pre_findings, "findings": []}
        post = {"findings_count": args.post_findings, "findings": [{"pattern": f"new-{i}"} for i in range(args.new_patterns)]}
        fix = {"applied": args.applied, "total_fixes": args.total}
        score = score_fix_gdpo("demo", fix, pre, post)
        print(f"  GDPO: {score['gdpo_overall']} {score['verdict']}")
        print(f"  D1(全过):{score['d1_full_pass']} D2(部分):{score['d2_partial_fix_rate']} D3(无退化):{score['d3_no_regression']}")

    elif args.cmd == "registry":
        reg_path = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills"))) / args.skill / "data" / "agent_skill_registry.json"
        if reg_path.exists():
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            print(f"  Agent Skill Registry: {args.skill}")
            print(f"  技能数: {reg['total_skills']}")
            print(f"  更新: {reg['iso_updated'][:19]}")
            for s in reg.get("skills", [])[-5:]:
                print(f"    [{s.get('skill_name', '?')}] {s.get('description', '')[:120]}")
        else:
            print("  (无注册表——运行 trace_distiller.py distill 来生成)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
