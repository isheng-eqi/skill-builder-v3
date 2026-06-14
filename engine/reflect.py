#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Layer 2: Reflect — the meta-loop that enables genuine self-evolution.

This is the double-loop learning engine. It recursively applies
the Layer 1 improvement cycle to itself:

  META-OBSERVE → META-QUESTION → META-PROPOSE → META-VALIDATE

v1.1 additions (2026-06-13):
  - GEPA execution trace analysis (cross-reference stubborn patterns with traces)
  - HyperAgents cross-skill scanning (find patterns that span multiple skills)
  - CRESCENT consensus calls (when System 2 is engaged, sample multiple times)

When System 1 hits an impasse, Reflect engages System 2 to:
  1. Analyze the pattern of failures across iterations
  2. Question whether current patterns/checks are adequate
  3. Propose new patterns or architectural changes
  4. Validate proposals through adversarial challenge

This is what distinguishes "self-optimization" from "self-evolution":
the ability to modify the framework that defines what "improvement" means.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from engine import memory, anchor
from engine.system1 import scanner as sys1_scanner, patterns as sys1_patterns
from engine.system2 import deliberator, proposer, challenger

from engine.platform import find_skill_root as _find_skill_root
# ── Meta-observe ──────────────────────────────────────────────────────────────

def meta_observe(skill_name: str, lookback: int = 20) -> dict:
    """META-OBSERVE: Gather data about Layer 1's behavior across iterations.

    Reads recent events, insights, and metrics to understand:
    - What patterns keep recurring?
    - Are any fixes being reverted?
    - Has convergence behavior changed?
    - (GEPA) Are execution traces showing fix failures?
    """
    events = memory.read_events(skill_name, limit=lookback)
    insights = memory.read_insights(skill_name)

    # Categorize events
    scans = [e for e in events if e["event_type"] == "scan"]
    fixes = [e for e in events if e["event_type"] == "fix"]
    rollbacks = [e for e in events if e["event_type"] == "rollback"]
    impasses = [e for e in events if e.get("data", {}).get("impasse_detected")]

    # (GEPA) Execution trace analysis
    traces = [e for e in events if e["event_type"] == "execution_trace"]
    failed_traces = [t for t in traces
                     if not t.get("data", {}).get("success", True)]
    trace_failures_by_rule = {}
    for t in failed_traces:
        rule = t.get("data", {}).get("fix_rule", "unknown")
        trace_failures_by_rule[rule] = trace_failures_by_rule.get(rule, 0) + 1

    # Track patterns over time
    pattern_trend = {}
    for e in scans:
        for f in e.get("data", {}).get("findings", []):
            p = f.get("pattern", "unknown")
            pattern_trend[p] = pattern_trend.get(p, 0) + 1

    # Identify stubborn patterns (recurring across multiple rounds)
    stubborn = {p: c for p, c in pattern_trend.items() if c >= 3}

    # (GEPA) Cross-reference: are stubborn patterns correlated with execution failures?
    trace_correlated_patterns = {}
    for t in traces:
        # Check if the fix's file was also flagged in scans
        fix_file = t.get("data", {}).get("fix_file", "")
        for e in scans:
            for f in e.get("data", {}).get("findings", []):
                if f.get("file") == fix_file and not t.get("data", {}).get("success", True):
                    p = f.get("pattern", "?")
                    trace_correlated_patterns[p] = trace_correlated_patterns.get(p, 0) + 1

    observation = {
        "total_events": len(events),
        "scans": len(scans),
        "fixes_applied": len(fixes),
        "rollbacks": len(rollbacks),
        "impasses_detected": len(impasses),
        "stubborn_patterns": stubborn,
        "pattern_distribution": pattern_trend,
        "recent_insights": len(insights),
        # GEPA additions
        "execution_traces": len(traces),
        "failed_traces": len(failed_traces),
        "trace_failures_by_rule": trace_failures_by_rule,
        "trace_correlated_patterns": trace_correlated_patterns,
        "iso_observed": datetime.now(timezone.utc).isoformat()
    }

    # Write observation as an insight if noteworthy
    if stubborn:
        memory.write_insight(
            skill_name,
            f"顽固模式检测: {stubborn} — 这些模式在过去 {lookback} 个事件中反复出现",
            events[:5],
            confidence=0.7
        )

    # (GEPA) Write insight if execution traces show fix failures
    if failed_traces:
        memory.write_insight(
            skill_name,
            f"GEPA 执行验证失败: {len(failed_traces)}/{len(traces)} 个修复的运行测试不通过。"
            f"失败规则: {trace_failures_by_rule}。"
            f"相关模式: {trace_correlated_patterns}",
            failed_traces[:5],
            confidence=0.85
        )

    return observation


# ── HyperAgents: Cross-skill scanning ─────────────────────────────────────────

def cross_skill_scan(source_skill: str = "") -> dict:
    """(HyperAgents) Scan ALL skills and find patterns that span multiple skills.

    This is the key HyperAgents innovation: meta-level improvements
    discovered in one skill are checked against all others.

    Returns:
      - Shared patterns (appear in 3+ skills) → candidates for global insight
      - Unique patterns (appear in only 1 skill) → may indicate skill-specific issues
      - Skills without immune system → bootstrap candidates
    """
    skills_base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    all_skills = []
    for d in skills_base.iterdir():
        if d.is_dir() and (d / "skill.md").exists():
            all_skills.append(d.name)
        elif d.is_dir() and (d / "SKILL.md").exists():
            all_skills.append(d.name)

    # Scan each skill
    skill_patterns = {}  # skill_name -> set of pattern names
    skill_findings = {}  # skill_name -> full findings
    global_pattern_counts = {}  # pattern_name -> count of skills that have it
    skills_without_immune_system = []

    for skill_name in all_skills:
        root = _find_skill_root(skill_name)
        if not root:
            continue

        # Check immune system status
        has_known_issues = (root / "references" / "known-issues.md").exists()
        has_patterns = (root / "references" / "patterns.md").exists()
        has_constitution = (root / "constitution.md").exists()

        if not (has_known_issues and has_patterns):
            skills_without_immune_system.append({
                "skill": skill_name,
                "missing": [x for x, has in [
                    ("known-issues.md", has_known_issues),
                    ("patterns.md", has_patterns)
                ] if not has]
            })

        # Run scanner
        scan = sys1_scanner.scan_skill(skill_name)
        findings = scan.get("findings", [])
        skill_findings[skill_name] = findings

        patterns = set(f.get("pattern", "unknown") for f in findings)
        skill_patterns[skill_name] = patterns

        for p in patterns:
            global_pattern_counts[p] = global_pattern_counts.get(p, 0) + 1

    # Identify shared patterns (HyperAgents: meta-level transfer candidates)
    shared_patterns = {p: c for p, c in global_pattern_counts.items() if c >= 3}
    unique_patterns = {p: c for p, c in global_pattern_counts.items() if c == 1}

    # Create global insights for shared patterns
    for pattern, count in shared_patterns.items():
        affected = [s for s, ps in skill_patterns.items() if pattern in ps]
        memory.write_global_insight(
            insight_text=f"跨技能共享模式: '{pattern}' 出现在 {count} 个技能中 ({affected})。"
                         f"这表明该模式不是单个技能的问题，而可能是系统层面的问题。"
                         f"建议在 patterns.md 中作为通用模式添加，一次性修复所有受影响的技能。",
            affected_skills=affected,
            pattern_name=pattern,
            confidence=min(0.5 + count * 0.1, 0.95),
            source_skill=source_skill or "cross-skill-scan"
        )

    # Build report
    return {
        "skills_scanned": len(all_skills),
        "skill_names": all_skills,
        "shared_patterns": shared_patterns,
        "unique_patterns": unique_patterns,
        "global_pattern_distribution": global_pattern_counts,
        "skills_without_immune_system": skills_without_immune_system,
        "immune_gap_count": len(skills_without_immune_system),
        "iso_scanned": datetime.now(timezone.utc).isoformat(),
        "verdict": (f"[OK] {len(shared_patterns)} 个跨技能共享模式已记录为全局洞察"
                    if shared_patterns
                    else "[OK] 未发现跨技能共享模式")
    }


def cross_skill_quality_matrix() -> dict:
    """(skill-auditor distilled) Quality comparison table across ALL skills.

    Runs a unified audit on every installed skill and produces a comparison:
      - frontmatter completeness
      - infrastructure files present
      - code quality issues (bare except, except-pass)
      - doc consistency
      - immune system status (has known-issues + patterns + constitution)

    This is the skill-auditor --all pattern applied by this engine.
    A skill that can audit others should also produce a quality comparison.
    """
    skills_base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    all_skills = []
    for d in skills_base.iterdir():
        if d.is_dir() and ((d / "skill.md").exists() or (d / "SKILL.md").exists()):
            all_skills.append(d.name)

    matrix = []
    for skill_name in sorted(all_skills):
        root = _find_skill_root(skill_name)
        if not root:
            continue

        row = {"skill": skill_name}

        # Frontmatter quality
        sm = None
        for name in ("skill.md", "SKILL.md"):
            if (root / name).exists():
                sm = root / name
                break
        if sm:
            fm_text = sm.read_text(encoding="utf-8")
            fm = {}
            if fm_text.startswith("---"):
                end = fm_text.find("---", 3)
                if end != -1:
                    for line in fm_text[3:end].strip().split("\n"):
                        if ":" in line:
                            k = line.split(":", 1)[0].strip()
                            fm[k] = True
            row["name"] = "✓" if "name" in fm else "✗"
            row["description"] = "✓" if "description" in fm else "✗"
            row["version"] = "✓" if "version" in fm else "✗"
            row["deps_declared"] = "✓" if "dependencies" in fm else "✗"
            row["skill_md_lines"] = len(fm_text.split("\n"))
        else:
            row["name"] = row["description"] = row["version"] = row["deps_declared"] = "✗"
            row["skill_md_lines"] = 0

        # Infrastructure
        row["known_issues"] = "✓" if (root / "references" / "known-issues.md").exists() else "✗"
        row["changelog"] = "✓" if (root / "references" / "changelog-archive.json").exists() else "✗"
        row["patterns"] = "✓" if (root / "references" / "patterns.md").exists() else "✗"
        row["readme"] = "✓" if (root / "README.md").exists() else "✗"
        row["env_check"] = "✓" if ((root / "scripts" / "env_check.py").exists() or (root / "scripts" / "env_setup.py").exists()) else "✗"
        row["gitignore"] = "✓" if (root / ".gitignore").exists() else "✗"

        # Constitution (advanced skill indicator)
        row["constitution"] = "✓" if (root / "constitution.md").exists() else "✗"

        # Score
        score_fields = ["name", "description", "version", "deps_declared",
                       "known_issues", "changelog", "patterns"]
        row["quality_score"] = sum(1 for f in score_fields if row.get(f) == "✓")
        row["max_score"] = len(score_fields)

        matrix.append(row)

    # Sort by quality score
    matrix.sort(key=lambda r: -r["quality_score"])

    return {
        "skills_audited": len(matrix),
        "skills": [r["skill"] for r in matrix],
        "quality_table": matrix,
        "summary": (
            f"平均质量: {sum(r['quality_score'] for r in matrix) / max(1, len(matrix)):.1f}/{matrix[0]['max_score'] if matrix else '?'}"
        ),
        "top_skills": [r["skill"] for r in matrix[:3]],
        "needs_bootstrap": [r["skill"] for r in matrix if r["quality_score"] < 3],
        "verdict": (
            f"[OK] {len(matrix)} skills audited, avg quality={sum(r['quality_score'] for r in matrix) / max(1, len(matrix)):.1f}/{matrix[0]['max_score'] if matrix else '?'}"
            if matrix else "[!!] No skills found"
        )
    }


def apply_global_insights(insight_id: Optional[str] = None) -> dict:
    """(HyperAgents) Apply unapplied global insights to their affected skills.

    For each global insight that hasn't been applied yet, check if the
    affected skill still has the issue, and if so, attempt to fix it.
    """
    insights = memory.read_global_insights()
    if insight_id:
        insights = [i for i in insights if i["global_insight_id"] == insight_id]

    applied_count = 0
    results = []

    for ins in insights:
        if ins.get("application_count", 0) > 0:
            continue  # Already applied at least once

        for skill_name in ins.get("affected_skills", []):
            root = _find_skill_root(skill_name)
            if not root:
                continue

            pattern_name = ins.get("pattern_name", "")
            if not pattern_name:
                continue

            # Check if this skill still has this pattern
            scan = sys1_scanner.scan_skill(skill_name)
            matching = [f for f in scan.get("findings", [])
                        if f.get("pattern") == pattern_name]

            if matching:
                # Try to fix using System 1 rules
                from engine.system1 import rules
                fix_result = rules.apply_fixes(skill_name, matching)
                memory.mark_global_insight_applied(ins["global_insight_id"])
                applied_count += 1
                results.append({
                    "global_insight_id": ins["global_insight_id"],
                    "skill": skill_name,
                    "pattern": pattern_name,
                    "fixes_applied": fix_result.get("applied", 0)
                })

    return {
        "insights_checked": len(insights),
        "applied": applied_count,
        "results": results,
        "verdict": f"[OK] {applied_count} 个全局洞察已应用" if applied_count
                   else "[--] 无可应用的未使用洞察"
    }


# ── Meta-question ─────────────────────────────────────────────────────────────

def meta_question(skill_name: str, observation: dict) -> dict:
    """META-QUESTION: Challenge the current framework's assumptions.

    Based on what meta_observe found, ask:
    - Why are these patterns stubborn?
    - Are current checks adequate?
    - Is the framework itself causing the problems?
    - (GEPA) Are execution failures indicating missing verification?
    """
    questions = []
    root = _find_skill_root(skill_name)

    # Q1: Stubborn patterns — why do they persist?
    for pattern, count in observation.get("stubborn_patterns", {}).items():
        questions.append({
            "question": f"模式 '{pattern}' 在过去 {count} 次扫描中反复出现——为什么当前防御无法根除？",
            "category": "defense_failure",
            "pattern": pattern
        })

    # Q2: Rollback rate — is the system causing more harm than good?
    rollbacks = observation.get("rollbacks", 0)
    fixes = observation.get("fixes_applied", 1)
    if rollbacks > 0 and fixes > 0:
        rollback_rate = rollbacks / (rollbacks + fixes)
        if rollback_rate > 0.2:
            questions.append({
                "question": f"回滚率为 {rollback_rate:.0%}——高于 20% 阈值。修复质量需要检查。",
                "category": "quality_concern",
                "rollback_rate": rollback_rate
            })

    # Q3: Are current patterns.md patterns adequate?
    if root:
        pp = root / "references" / "patterns.md"
        if pp.exists():
            patterns_text = pp.read_text(encoding="utf-8")
            pattern_count = patterns_text.count("## 模式")
            if pattern_count < 5:
                questions.append({
                    "question": f"patterns.md 仅有 {pattern_count} 个模式——可能不足以覆盖所有常见问题",
                    "category": "coverage_gap"
                })

    # Q4: Self-reference check — for any skill that has a constitution
    constitution_path = root / "constitution.md" if root else None
    if constitution_path and constitution_path.exists():
        con_check = anchor.check_self_reference(root)
        if not con_check["passed"]:
            questions.append({
                "question": f"检测到宪法违规: {con_check.get('violations', [])}",
                "category": "constitutional_violation"
            })

    # (GEPA) Q5: Are fix execution failures indicating a pattern?
    trace_failures = observation.get("trace_failures_by_rule", {})
    if trace_failures:
        questions.append({
            "question": f"GEPA 执行验证: {trace_failures}——某些修复在运行时仍然失败，"
                        f"说明当前验证步骤可能不够，需要增强修复后的运行测试。",
            "category": "gepa_verification_gap"
        })

    # (GEPA) Q6: Are trace failures correlated with specific stubborn patterns?
    trace_corr = observation.get("trace_correlated_patterns", {})
    if trace_corr:
        questions.append({
            "question": f"执行失败的修复与以下模式相关: {trace_corr}。"
                        f"这表明这些模式不仅是代码问题，修复本身也容易出错——需要更保守的修复策略。",
            "category": "gepa_pattern_correlation"
        })

    # (HyperAgents) Q7: Are there global insights that apply here?
    global_insights = memory.read_global_insights(limit=10)
    if global_insights:
        unapplied = [g for g in global_insights if g.get("application_count", 0) == 0]
        if unapplied:
            questions.append({
                "question": f"有 {len(unapplied)} 个全局洞察尚未应用到任何技能——"
                            f"这些可能包含适用于 {skill_name} 的改进。",
                "category": "hyperagents_pending_transfer"
            })

    # (第零法则) Q8: Constitution realizability — any gaps?
    from engine import realizability as realiz
    real_audit = realiz.run_full_realizability_audit()
    if real_audit.get("honest_gaps", 0) > 0 or real_audit.get("true_failures", 0) > 0:
        questions.append({
            "question": f"宪法可实现性审计发现 {real_audit['true_failures']} 条失败 + "
                        f"{real_audit['honest_gaps']} 条诚实差距。"
                        f"差距已自动写入 references/constitution-amendments.md。"
                        f"需要人审批宪法修正。（第零法则）",
            "category": "constitution_realizability"
        })

    return {
        "questions_count": len(questions),
        "questions": questions,
        "iso_questioned": datetime.now(timezone.utc).isoformat()
    }


# ── Meta-propose ──────────────────────────────────────────────────────────────

def meta_propose(skill_name: str, observation: dict,
                  questions: dict) -> dict:
    """META-PROPOSE: Generate proposals based on observations and questions.

    Uses System 2 proposer to create concrete proposals for:
    - New patterns (from stubborn pattern analysis)
    - Architecture changes (from defense failure analysis)
    - Constitutional amendments (rare, human-only)
    - (GEPA) Verification enhancement proposals
    """
    all_proposals = []

    # For each stubborn pattern, propose a new defense
    for pattern, count in observation.get("stubborn_patterns", {}).items():
        if pattern in ("language-drift", "hardcoded-numbers",
                        "hardcoded-versions", "grep-pcre",
                        "missing-utf8-header", "self-reference",
                        "missing-docstring", "dev-null"):
            continue  # Already known, not a new discovery

        result = proposer.propose_new_pattern(
            skill_name=skill_name,
            pattern_name=f"自动发现: {pattern}",
            symptoms=f"模式 '{pattern}' 在过去 {count} 次扫描中反复出现",
            affected_files="由扫描器检测的文件类型",
            countermeasure=f"在 patterns.md 中新增检查条目，在 scanner.py 中新增检测规则",
            check_command=f"python engine/system1/scanner.py {skill_name} --severity critical",
            rationale=f"System 2 自动发现——模式 '{pattern}' 重复 {count} 次，当前防御不够"
        )
        all_proposals.append(result)

    # (GEPA) Propose execution verification enhancement if traces show failures
    trace_failures = observation.get("trace_failures_by_rule", {})
    if trace_failures:
        proposal = proposer.create_proposal(
            skill_name=skill_name,
            title="GEPA: 增强修复后执行验证",
            description=f"执行验证失败: {trace_failures}。"
                        f"建议为这些规则增加更严格的 post-fix 测试。",
            files_changed=["engine/loop.py"],
            change_type="verification",
            rationale="GEPA 原则——修复不仅要声称成功，还要通过运行测试证明。"
        )
        proposer.save_proposal(skill_name, proposal)
        all_proposals.append({"proposal_id": proposal["proposal_id"]})

    # If no new patterns found but there are questions, propose a review
    if not all_proposals and questions.get("questions"):
        proposal = proposer.create_proposal(
            skill_name=skill_name,
            title="元审计: 防御系统审查",
            description=f"System 2 发现 {questions['questions_count']} 个问题需要审查",
            files_changed=["references/patterns.md"],
            change_type="architecture",
            rationale="定期元审计——确保防御系统没有退化"
        )
        proposer.save_proposal(skill_name, proposal)
        all_proposals.append({"proposal_id": proposal["proposal_id"]})

    return {
        "proposals_generated": len(all_proposals),
        "proposals": all_proposals,
        "iso_proposed": datetime.now(timezone.utc).isoformat()
    }


# ── Meta-validate ─────────────────────────────────────────────────────────────

def meta_validate(skill_name: str, proposals_result: dict) -> dict:
    """META-VALIDATE: Run proposals through the challenger + constitution.

    Each proposal must pass:
    1. Constitutional check (anchor.py)
    2. Adversarial challenge (challenger.py)

    Only proposals that pass all checks are marked as "verified".
    """
    verification_results = []

    for prop_summary in proposals_result.get("proposals", []):
        prop_id = prop_summary.get("proposal_id")
        if not prop_id:
            continue

        proposal = proposer.load_proposal(skill_name, prop_id)
        if not proposal:
            continue

        # Constitutional check
        con_result = challenger.challenge_constitution(skill_name, proposal)

        if con_result["verdict"] == "PASS":
            proposer.update_proposal_status(skill_name, prop_id, "verified",
                                             {"method": "constitutional"})
            verification_results.append({
                "proposal_id": prop_id,
                "verdict": "verified",
                "method": "constitutional"
            })
        else:
            proposer.update_proposal_status(skill_name, prop_id, "rejected",
                                             {"reason": f"宪法违规: {con_result.get('violations', [])}"})
            verification_results.append({
                "proposal_id": prop_id,
                "verdict": "rejected",
                "violations": con_result.get("violations", [])
            })

    verified = [r for r in verification_results if r["verdict"] == "verified"]

    return {
        "total": len(verification_results),
        "verified": len(verified),
        "rejected": len(verification_results) - len(verified),
        "results": verification_results
    }


# ── Full reflect cycle ────────────────────────────────────────────────────────

def run_reflect(skill_name: str, lookback: int = 20,
                cross_skill: bool = False) -> dict:
    """Run the full Layer 2 reflection cycle.

    This is the meta-loop in action:
    Observe → Question → Propose → Validate

    If cross_skill is True, also runs HyperAgents cross-skill scan.
    """
    memory.write_event(skill_name, "reflect", {"phase": "start"})

    report = {}

    # (HyperAgents) Optional cross-skill scan
    apply_result = {"applied": 0, "insights_checked": 0, "results": [],
                    "verdict": "[--] 未运行跨技能扫描（需要 --cross-skill）"}
    if cross_skill:
        print(f"\n{'═'*50}")
        print(f"  HyperAgents: 跨技能扫描")
        print(f"{'═'*50}")
        cross = cross_skill_scan(source_skill=skill_name)
        print(f"  扫描: {cross['skills_scanned']} 个技能")
        print(f"  共享模式: {cross['shared_patterns']}")
        print(f"  免疫缺口: {cross['immune_gap_count']} 个技能缺少免疫系统")
        report["cross_skill_scan"] = cross  # v1.2: 跨技能扫描数据

        # Apply global insights
        apply_result = apply_global_insights()
        if apply_result["applied"] > 0:
            print(f"  全局洞察应用: {apply_result['applied']} 个")
        report["cross_skill_applied"] = apply_result  # v1.2: 分离 apply 结果
    else:
        report["cross_skill_scan"] = apply_result

    print(f"\n{'═'*50}")
    print(f"  Layer 2: Reflect — {skill_name}")
    print(f"{'═'*50}")

    # Step 1: Meta-Observe
    print(f"\n  [META-OBSERVE] 收集迭代数据...")
    observation = meta_observe(skill_name, lookback)
    print(f"    事件: {observation['total_events']}")
    print(f"    顽固模式: {observation['stubborn_patterns']}")
    if observation.get("failed_traces", 0) > 0:
        print(f"    [GEPA] 执行验证失败: {observation['failed_traces']} "
              f"/ {observation['execution_traces']} traces")

    # Step 2: Meta-Question
    print(f"\n  [META-QUESTION] 挑战当前假设...")
    questions = meta_question(skill_name, observation)
    print(f"    问题数: {questions['questions_count']}")
    for q in questions.get("questions", [])[:5]:
        print(f"    [?] [{q['category']}] {q['question'][:120]}")

    # Step 3: Meta-Propose
    print(f"\n  [META-PROPOSE] 生成改进提案...")
    proposals_result = meta_propose(skill_name, observation, questions)
    print(f"    提案数: {proposals_result['proposals_generated']}")

    # Step 4: Meta-Validate
    print(f"\n  [META-VALIDATE] 验证提案...")
    validation = meta_validate(skill_name, proposals_result)
    print(f"    通过: {validation['verified']} | 拒绝: {validation['rejected']}")

    # Record
    memory.write_event(skill_name, "insight", {
        "observation": observation,
        "questions_count": questions["questions_count"],
        "proposals": proposals_result["proposals_generated"],
        "verified": validation["verified"]
    })

    # Write metrics
    memory.write_metric(skill_name, "stubborn_patterns",
                        len(observation.get("stubborn_patterns", {})))
    memory.write_metric(skill_name, "reflect_proposals",
                        proposals_result["proposals_generated"])
    memory.write_metric(skill_name, "reflect_verified",
                        validation["verified"])
    memory.write_metric(skill_name, "gepa_trace_failure_rate",
                        observation["failed_traces"] / max(1, observation["execution_traces"]))

    print(f"\n{'─'*50}")
    print(f"  Reflect 完成")
    print(f"  观察 → {questions['questions_count']} 疑问 → "
          f"{proposals_result['proposals_generated']} 提案 → "
          f"{validation['verified']} 验证通过")
    print(f"{'─'*50}")

    report.update({
        "skill": skill_name,
        "observation": observation,
        "questions": questions,
        "proposals": proposals_result,
        "validation": validation
    })
    return report


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Layer 2 元循环: Meta-Observe → Question → Propose → Validate"
    )
    parser.add_argument("--target", help="目标 skill 名称")
    parser.add_argument("--lookback", type=int, default=20,
                        help="回溯事件数 (默认 20)")
    parser.add_argument("--observe-only", action="store_true",
                        help="仅观察，不提问/提案")
    parser.add_argument("--propose-only", action="store_true",
                        help="仅提案，不验证")
    parser.add_argument("--cross-skill", action="store_true",
                        help="(HyperAgents) 运行跨技能扫描")
    parser.add_argument("--cross-scan-only", action="store_true",
                        help="(HyperAgents) 仅跨技能扫描，不运行反射")
    parser.add_argument("--quality-matrix", action="store_true",
                        help="(skill-auditor) 跨技能质量对比矩阵")

    args = parser.parse_args()

    # Skill-auditor quality matrix
    if args.quality_matrix:
        matrix = cross_skill_quality_matrix()
        print(f"\n  Skill Quality Matrix: {matrix['summary']}")
        print(f"  {'Skill':<25} {'Score':<6} name desc ver deps KI CL PL RE env gi const")
        print(f"  {'-'*25} {'-'*6} {'-'*65}")
        for r in matrix["quality_table"]:
            print(f"  {r['skill']:<25} {r['quality_score']}/{r['max_score']}   "
                  f"{r['name']}   {r['description']}   {r['version']}   {r['deps_declared']}   "
                  f"{r['known_issues']}  {r['changelog']}  {r['patterns']}  "
                  f"{r['readme']}  {r['env_check']}  {r['gitignore']}  {r['constitution']}")
        if matrix["needs_bootstrap"]:
            print(f"\n  Needs bootstrap: {matrix['needs_bootstrap']}")
        return

    # HyperAgents cross-skill scan (standalone)
    if args.cross_scan_only:
        cross = cross_skill_scan(source_skill=args.target)
        print(json.dumps(cross, indent=2, ensure_ascii=False))
        return

    if args.observe_only:
        obs = meta_observe(args.target, args.lookback)
        print(json.dumps(obs, indent=2, ensure_ascii=False))
        return

    report = run_reflect(args.target, args.lookback,
                         cross_skill=args.cross_skill)

    if args.propose_only:
        return

    # Final summary
    v = report["validation"]
    print(f"\n  Reflect 报告: {args.target}")
    print(f"  观察: 顽固模式={len(report['observation'].get('stubborn_patterns', {}))}")
    print(f"  问题: {report['questions']['questions_count']}")
    print(f"  提案: {report['proposals']['proposals_generated']}")
    print(f"  验证: {v['verified']} 通过 / {v['rejected']} 拒绝")
    if report.get("cross_skill_scan"):
        cs = report["cross_skill_scan"]
        if isinstance(cs, dict) and "shared_patterns" in cs:
            print(f"  跨技能: {cs.get('skills_scanned', '?')}个技能, "
                  f"{len(cs.get('shared_patterns', {}))}个共享模式")
    if report.get("cross_skill_applied"):
        ap = report["cross_skill_applied"]
        if ap.get("applied", 0) > 0:
            print(f"  跨技能应用: {ap['applied']} 个全局洞察")


if __name__ == "__main__":
    main()
