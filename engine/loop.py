#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Layer 1: Improvement Loop — Generate→Evaluate→Select→Integrate→Verify

The core self-improvement engine. Orchestrates:
  System 1 (fast): scanner → pattern matcher → rules → fixes
  System 2 (slow, on impasse): deliberator → proposer → challenger

v1.1 additions (2026-06-13):
  - GEPA execution trace verification (step 5: run skill after fix, record output)
  - TAPO per-turn credit assignment (fix fingerprints, rollback correlation)
  - GVU stability gating (check verifier SNR before applying destructive fixes)
  - DGM archive snapshots (periodic diversity-preserving snapshots)

All operations use <skill-name> as the sole parameter.
No special paths for skill-builder-v3 itself.

This is the universal self-improvement loop distilled from:
  SOAR (impasse → subgoal → chunk),
  AlphaGo Zero (self-play),
  K8s (reconciliation loop),
  OpenHands (code-as-action loop),
  GEPA (execution trace feedback),
  TAPO/ReVeal (per-turn credit assignment),
  GVU Operator (verifier SNR gating),
  Darwin Godel Machine (archive diversity).
"""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add engine to path for sibling imports
HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from engine.platform import find_skill_root as _find_skill_root
from engine.system1 import scanner, patterns as pat, rules
from engine import memory, anchor

# ═══════════════════════════════════════════════════════════════════════════════
# v4: 集中化可选导入——不再静默吞掉 ImportError。
# 当可选模块不可用时，记录模块名到 data/logs/ 而非静默失效。
# 这使得模块路径变更产生的功能消失变为可检测的。
# ═══════════════════════════════════════════════════════════════════════════════

def _warn_import(skill_name: str, module_name: str, context: str = "") -> None:
    """Log a warning when an optional module fails to import.

    Replaces the previous pattern of 'except ImportError: pass  # TODO: log or re-raise'.
    v4: The TODO is now DONE — we actually log the warning.
    """
    try:
        memory.write_event(skill_name, "loop", {
            "phase": "optional_import_missing",
            "module": module_name,
            "context": context,
            "_v4_honesty": "This module is optional — the system continues without it. "
                          "But if this appears persistently, investigate why."
        })
    except Exception:
        # Fallback: if even memory.write_event fails, use stderr
        import sys as _sys
        print(f"[WARN] optional module '{module_name}' unavailable ({context})",
              file=_sys.stderr)


# ── Design params ─────────────────────────────────────────────────────────────

def _load_params(skill_name: str) -> dict:
    """Load design parameters for a skill. Falls back to defaults."""
    root = _find_skill_root(skill_name)
    if not root:
        return _defaults()

    dp_path = root / "design_params.json"
    manifest_path = root / "manifest.json"

    params = _defaults()

    if dp_path.exists():
        try:
            dp = json.loads(dp_path.read_text(encoding="utf-8"))
            for k, v in dp.get("params", {}).items():
                if "value" in v:
                    params[k] = v["value"]
        except (json.JSONDecodeError, OSError):
            pass  # TODO: log or re-raise

    if manifest_path.exists():
        try:
            mf = json.loads(manifest_path.read_text(encoding="utf-8"))
            for k, v in mf.get("design_params", {}).items():
                if k not in params:
                    params[k] = v
        except (json.JSONDecodeError, OSError):
            pass  # TODO: log or re-raise

    return params


def _defaults() -> dict:
    return {
        "stop_rounds": 2,
        "meta_audit_interval": 5,
        "max_iterations": 10,
        "autonomy_level": "cold_start",
        "gvu_snr_threshold": 1.0,     # GVU: stop if verifier SNR drops below this
        "archive_interval": 10,        # DGM: archive snapshot every N rounds
        "exec_trace_sample_rate": 1.0, # GEPA: verify fraction of fixes (1.0 = all)
        "observer_interval": 5,        # (v1.3) Observer 主动触发间隔——每N轮强制运行 Reflect
        "auto_fix_suspend_threshold": 0.3,  # (v1.3) 证据通过率低于此值时暂停自动修复
    }


# ── Layer 1 cycle ─────────────────────────────────────────────────────────────

def generate_fixes(skill_name: str) -> dict:
    """GENERATE: Run System 1 fast scan + pattern audit. Return findings."""
    # System 1 scan
    scan = scanner.scan_skill(skill_name)
    findings = scan.get("findings", [])

    # Pattern audit
    pa = pat.audit_patterns(skill_name)
    findings.extend(pa.get("findings", []))

    # Deduplicate by (pattern, file, line)
    seen = set()
    unique = []
    for f in findings:
        key = (f.get("pattern", ""), f.get("file", ""), f.get("line", 0))
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return {
        "skill": skill_name,
        "findings": unique,
        "findings_count": len(unique),
        "source": {"scan": scan.get("findings_count", 0),
                   "patterns": pa.get("findings_count", 0)}
    }


def evaluate_fixes(skill_name: str, findings: list) -> dict:
    """EVALUATE: Apply rules to findings, determine which fixes are applicable.

    For System 1: rules engine determines applicability.
    For System 2 (future): LLM evaluates complex findings.
    """
    root = _find_skill_root(skill_name)

    # Determine which rules apply
    applicable = rules.get_applicable_rules(findings, skill_name)

    # For each finding without an auto-fix rule, check if System 2 should handle it
    handled_patterns = {r[0]["pattern"] for r in applicable}
    needs_system2 = [f for f in findings if f["pattern"] not in handled_patterns]

    # TAPO: Check per-rule fix quality and demote low-quality rules
    rule_quality_report = {}
    for rule_entry, _ in applicable:
        rname = rule_entry["name"]
        if rname not in rule_quality_report:
            q = memory.get_fix_quality(skill_name, rname)
            rule_quality_report[rname] = q
            # If a rule has < 50% success rate over last 10 fixes, flag it
            if q["total"] >= 5 and q.get("last_10_rate", 1.0) < 0.5:
                rule_entry["_quality_warning"] = True
                rule_entry["_success_rate"] = q["last_10_rate"]

    return {
        "auto_fixable": len(applicable),
        "needs_system2": len(needs_system2),
        "auto_rules": [{"rule": r[0]["name"], "file": r[1]["file"]} for r in applicable],
        "system2_candidates": [f["pattern"] for f in needs_system2[:5]],
        "findings": findings,
        "rule_quality": rule_quality_report
    }


def select_fixes(skill_name: str, evaluation: dict) -> list:
    """SELECT: Choose which fixes to apply.

    In cold_start/supervised: applies all auto-fixable ones.
    In trusted: uses GVU SNR gating — skips fixes from low-quality rules.
    """
    params = _load_params(skill_name)

    # Check GVU stability before selecting destructive fixes
    gvu = memory.calculate_gvu_snr(skill_name)
    if not gvu["stable"] and params.get("autonomy_level") == "trusted":
        # GVU gate: skip fixes that change files when SNR is below threshold
        selected = evaluation.get("auto_rules", [])
        return [s for s in selected
                if not any(kw in s.get("rule", "") for kw in ["fix-grep", "tag-hardcoded"])]

    # Default: apply all auto-fixable
    return evaluation.get("auto_rules", [])


def integrate_fixes(skill_name: str, selected: list, findings: list) -> dict:
    """INTEGRATE: Apply fixes, create snapshot, update memory.

    Follows the constitutional verify-before-adopt principle:
    1. Create snapshot before changing anything
    2. Apply fixes
    3. Verify basic functionality still works
    4. Write event logs
    """
    root = _find_skill_root(skill_name)

    # Any skill with constitution.md and engine/ changes gets constitutional verification.
    constitution_path = root / "constitution.md"
    files_to_change = [s.get("file", "") for s in selected if "engine/" in str(s.get("file", ""))]
    if constitution_path.exists() and files_to_change:
            check = anchor._run_pre_modify_checks(root)
            if not check["all_passed"]:
                return {"error": "宪法验证失败——修改被拒绝",
                        "failed_checks": check}

    # Create snapshot
    snapshot_id = anchor.create_snapshot(root, f"loop-integration: {len(selected)} fixes")
    if not snapshot_id:
        snapshot_id = "none"

    # Apply fixes
    fix_report = rules.apply_fixes(skill_name, findings)

    # Verify
    files_changed = fix_report.get("files_changed", [])
    if files_changed:
        post_check = anchor._run_pre_modify_checks(root)
        if not post_check["all_passed"]:
            # Rollback!
            anchor.rollback(snapshot_id, root)
            return {"error": "修复后验证失败——已自动回滚",
                    "snapshot": snapshot_id,
                    "failed_checks": post_check}

    # Record events
    memory.write_event(skill_name, "scan",
                       {"findings_count": len(findings)})
    memory.write_event(skill_name, "fix",
                       {"fixes": fix_report, "snapshot": snapshot_id})
    memory.write_event(skill_name, "integrate",
                       {"snapshot_id": snapshot_id,
                        "files_changed": files_changed})

    # Update known-issues if fixes were applied
    if fix_report.get("applied", 0) > 0:
        _update_known_issues(skill_name, fix_report)

    # (v1.2) Track probation for generated rules — record each fix outcome
    for fix in fix_report.get("fixes", []):
        rule_name = fix.get("rule", "")
        if not rule_name:
            continue
        try:
            from engine.system2 import rule_generator as rg
            gen_rules = rg.load_all_generated_rules(skill_name)
            for gr in gen_rules:
                if gr["name"] == rule_name and gr.get("status") == "probation":
                    success = fix.get("applied", False)
                    outcome = rg.record_probation_fix(
                        skill_name, gr["rule_id"], success,
                        evidence_span_id=f"evt-{int(time.time())}",
                        verdict="GEPA verified" if success else fix.get("reason", "unknown")
                    )
                    if outcome and "new_status" in outcome:
                        if outcome["new_status"] == "active":
                            print(f"  [PROBATION→ACTIVE] {rule_name} 晋升! "
                                  f"({outcome['probation']['successes']}/{outcome['probation']['successes']+outcome['probation']['failures']})")
                        elif outcome["new_status"] == "deprecated":
                            print(f"  [PROBATION→DEPRECATED] {rule_name} 废弃 "
                                  f"(失败 {outcome['probation']['failures']})")
        except ImportError as _ie:
            _warn_import(skill_name, "optional_module", str(_ie))

    return {
        "snapshot_id": snapshot_id,
        "fixes_applied": fix_report.get("applied", 0),
        "fixes_skipped": fix_report.get("skipped", 0),
        "files_changed": files_changed,
        "fix_report": fix_report
    }


# ── (P3: Ultragoal) Independent verification ──────────────────────────────────

def verify_execution_independent(skill_name: str, integration_result: dict) -> dict:
    """(P3: Ultragoal) Independent verifier — fresh eyes on the result.

    Key insight from Anthropic's research and ultragoal:
      A verifier that knows HOW the fix was done cannot be trusted.
      The independent verifier only sees BEFORE/AFTER state — not the fix method.

    This is run as a separate subprocess that re-scans the skill from scratch,
    building its verdict independently. If the independent verdict contradicts
    the self-verification, System 2 is triggered.

    v1.2: Also runs P2 (EVE-Agent) evidence verification on all fixes.
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": "Skill not found", "agreement": False}

    if integration_result.get("fixes_applied", 0) == 0:
        return {"independent_verdict": "[OK] 无修复",
                "agreement": True, "should_escalate": False,
                "self_verified": True,
                "evidence_pass_rate": 1.0,
                "evidence_summary": "[OK] 无修复需要证据",
                "post_fix_findings": 0}

    # Run evidence verification (P2: EVE-Agent)
    from engine import evidence as ev
    fix_report = integration_result.get("fix_report", {})
    fixes = fix_report.get("fixes", [])
    applied_fixes = [f for f in fixes if f.get("applied")]
    evidence_results = ev.verify_all_fix_evidences(fixes, root)

    # Detect tag-only fixes — these annotate patterns, they don't remove them.
    # For tag fixes, verify tag presence rather than re-scanning for pattern removal.
    tag_rules = [f for f in applied_fixes
                 if f.get("rule", "").startswith("tag-")]
    all_tag_fixes = tag_rules and len(tag_rules) == len(applied_fixes)

    if all_tag_fixes:
        # Tag-only fix batch: verify each tag is present in its file.
        # v4.2: fix-bare-except (formerly tag-bare-except) is now a GENUINE FIX
        # that replaces except: with except Exception:. It uses standard re-scan
        # verification — not the tag-only path.
        tag_map = {
            "tag-except-pass": "add logging",
            "tag-hardcoded-versions": "consider dynamic version lookup",
        }
        post_fix_findings = 0
        independent_ok = True
        for fix in applied_fixes:
            fp = root / fix.get("file", "")
            if fp.exists():
                expected = tag_map.get(fix.get("rule", ""), "TODO")
                content = fp.read_text(encoding="utf-8")
                if expected in content:
                    post_fix_findings += 1
                else:
                    independent_ok = False
        # For tag fixes, evidence threshold is relaxed (annotation-only changes
        # produce minimal evidence beyond the tag text itself)
        independent_ok = independent_ok and evidence_results.get("evidence_pass_rate", 0.0) >= 0.3
    else:
        # Standard independent verification: re-scan the skill in an isolated subprocess.
        # This subprocess has NO knowledge of which files were changed or how.
        try:
            scan_cmd = (
                f'"{sys.executable}" -c "'
                f'import sys; sys.path.insert(0, r\'{root}\'); '
                f'from engine.system1 import scanner; '
                f'r = scanner.scan_skill(\'{skill_name}\'); '
                f'print(r.get(\\\'findings_count\\\', -1))"'
            )
            result = subprocess.run(
                scan_cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=str(root), encoding="utf-8", errors="replace"
            )
            post_fix_findings = int(result.stdout.strip()) if result.stdout.strip().isdigit() else -1
            independent_ok = post_fix_findings >= 0
        except Exception:
            post_fix_findings = -1
            independent_ok = False

        # Build independent verdict
        independent_ok = independent_ok and evidence_results.get("evidence_pass_rate", 0.0) >= 0.6
    independent_verdict = {
        "post_fix_findings": post_fix_findings,
        "scanner_accessible": independent_ok,
        "evidence_pass_rate": evidence_results.get("evidence_pass_rate", 0.0),
        "evidence_details": evidence_results.get("verdict", "?"),
        "verdict": "[OK] 独立验证通过" if independent_ok
                   else "[!!] 独立验证未通过"
    }

    # Self-verification (original GEPA trace)
    self_result = verify_execution(skill_name, integration_result)

    # Agreement: do independent and self agree?
    agreement = (
        independent_verdict.get("verdict", "").startswith("[OK]") ==
        self_result.get("verified", False)
    )

    # Escalate to System 2 if independent and self disagree
    should_escalate = not agreement

    # Always record execution trace (GEPA: verify every fix)
    memory.write_event(skill_name, "execution_trace", {
        "phase": "independent_verification",
        "success": agreement,
        "verdict": "agreement" if agreement else "disagreement",
        "independent": independent_verdict,
        "self": {"verified": self_result.get("verified", False)}
    })

    if should_escalate:
        memory.write_insight(
            skill_name,
            f"独立验证分歧: 自我验证={'通过' if self_result.get('verified') else '失败'}, "
            f"独立验证={'通过' if independent_ok else '失败'}. "
            f"证据通过率: {evidence_results.get('evidence_pass_rate', 0):.0%}. "
            f"修复方法: {[f.get('rule', '?') for f in fixes if f.get('applied')]}",
            [], confidence=0.9
        )

    # Record evidence to memory index
    for fix in fixes:
        if fix.get("applied") and fix.get("file"):
            ev.record_evidence_to_memory(
                skill_name,
                ev.collect_compile_evidence(root / fix["file"]),
                fix_rule=fix.get("rule", "?"),
                fix_file=fix.get("file", "?")
            )

    return {
        "independent_verdict": independent_verdict["verdict"],
        "post_fix_findings": post_fix_findings,
        "self_verified": self_result.get("verified", False),
        "agreement": agreement,
        "should_escalate": should_escalate,
        "evidence_pass_rate": evidence_results.get("evidence_pass_rate", 0.0),
        "evidence_summary": evidence_results.get("verdict", "?")
    }


# ── (GEPA) Execution trace verification ───────────────────────────────────────

def verify_execution(skill_name: str, integration_result: dict) -> dict:
    """(GEPA) Verify fixes by actually running the skill's self-checks.

    After integrate_fixes, this step:
    1. Runs the skill's self-test/scan command
    2. Captures stdout/stderr as execution_trace
    3. Compares pre-fix and post-fix execution output
    4. Flags fixes whose execution shows errors

    This is the key GEPA insight: don't just claim "fixed" — prove it by running.
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {"verified": False, "error": "Skill not found"}

    params = _load_params(skill_name)
    sample_rate = params.get("exec_trace_sample_rate", 1.0)

    # Skip if no fixes were applied
    if integration_result.get("fixes_applied", 0) == 0:
        return {"verified": True, "fixes_tested": 0,
                "verdict": "[OK] 无修复需要验证"}

    verification_results = []
    fix_report = integration_result.get("fix_report", {})

    for fix in fix_report.get("fixes", []):
        if not fix.get("applied"):
            continue

        rule_name = fix.get("rule", "unknown")
        fix_file = fix.get("file", fix.get("finding_file", "?"))

        # Build verification command based on what was fixed
        verification_cmds = _build_verification_commands(root, fix)

        for cmd_tuple in verification_cmds:
            # v4: unpack 3-tuple (label, command, verification_tier)
            if len(cmd_tuple) == 3:
                cmd_desc, cmd, verif_tier = cmd_tuple
            else:
                cmd_desc, cmd = cmd_tuple[0], cmd_tuple[1]
                verif_tier = "unknown"
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=30, cwd=str(root), encoding="utf-8", errors="replace"
                )
                exit_code = result.returncode
                stdout_tail = result.stdout[-500:] if result.stdout else ""
                stderr_tail = result.stderr[-500:] if result.stderr else ""

                success = exit_code == 0
                trace = {
                    "fix_rule": rule_name,
                    "fix_file": fix_file,
                    "command": cmd_desc,
                    "exit_code": exit_code,
                    "success": success,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "verification_tier": verif_tier  # v4: 标记验证层级
                }
                verification_results.append(trace)

                # Record execution trace event
                memory.write_event(skill_name, "execution_trace", trace)

                # v4: TAPO quality —— "cannot_verify" tier doesn't count as success or failure
                # It's an honest admission, not a pass.
                if verif_tier != "cannot_verify":
                    memory.write_fix_quality(skill_name, rule_name, success, {
                        "file": fix_file,
                        "command": cmd_desc,
                        "verification_tier": verif_tier,
                        "snapshot": integration_result.get("snapshot_id", "?")
                    })

            except subprocess.TimeoutExpired:
                verification_results.append({
                    "fix_rule": rule_name, "fix_file": fix_file,
                    "command": cmd_desc, "success": False,
                    "error": "timeout after 30s"
                })
                memory.write_fix_quality(skill_name, rule_name, False,
                                         {"file": fix_file, "error": "timeout"})
            except Exception as e:
                verification_results.append({
                    "fix_rule": rule_name, "fix_file": fix_file,
                    "command": cmd_desc, "success": False,
                    "error": str(e)
                })

    # ── v4 Tier-aware aggregate ──
    tested = len(verification_results)
    passed = sum(1 for v in verification_results if v.get("success"))
    failed = tested - passed
    semantic_tests = [v for v in verification_results if v.get("verification_tier") == "semantic"]
    cannot_verify = [v for v in verification_results if v.get("verification_tier") == "cannot_verify"]
    semantic_passed = sum(1 for v in semantic_tests if v.get("success"))
    semantic_failed = len(semantic_tests) - semantic_passed

    # v4 honesty: if all verification is "cannot_verify" tier, we cannot claim success
    if cannot_verify and not semantic_tests:
        verified = False
        verdict = (f"[!!] 无法语义验证 — {len(cannot_verify)} 条验证均为"
                   f" 'cannot_verify' 级。修复是伪修复（仅加注释）——"
                   f" 无法证明问题已被解决。")
    elif semantic_failed > 0:
        verified = False
        verdict = (f"[!!] 语义验证失败: {semantic_failed}/{len(semantic_tests)} "
                   f"个语义测试不通过 ({failed}/{tested} 总计)")
    else:
        verified = True
        verdict = ("[OK] 全部 {0} 个修复的验证通过 ({1} 语义 + {2} 语法)".format(
            passed, semantic_passed, passed - semantic_passed)
            if failed == 0
            else "[!!] {0}/{1} 个修复验证失败".format(failed, tested))

    # If any fix failed, write an insight
    if not verified:
        failed_rules = [v["fix_rule"] for v in verification_results if not v.get("success")]
        if cannot_verify:
            failed_rules = list(set(failed_rules + [v["fix_rule"] for v in cannot_verify]))
        memory.write_insight(
            skill_name,
            f"GEPA 执行验证: {'语义失败' if semantic_failed > 0 else ''}"
            f"{' 无法语义验证' if cannot_verify else ''}。"
            f"语义测试: {semantic_passed}/{len(semantic_tests)} 通过。"
            f"无法验证: {len(cannot_verify)}。"
            f"涉及规则: {failed_rules[:10]}",
            [],
            confidence=0.8
        )

    return {
        "verified": failed == 0,
        "fixes_tested": tested,
        "passed": passed,
        "failed": failed,
        "verdict": verdict,
        "details": verification_results
    }


def _build_verification_commands(root: Path, fix: dict) -> list:
    """Build verification commands that test whether the PROBLEM was solved,
    not whether the FIX OPERATION was applied.

    ⚠️  v4 诚实声明: tag-* 规则是伪修复（只加注释）——无法验证"问题被解决"，
    只能诚实标记为 CANNOT_SEMANTICALLY_VERIFY。
    对于真正的修复，验证命令测试的是问题的消失，而非操作的痕迹。

    Each returned tuple: (label, shell_command, verification_tier)
    verification_tier:
      - "semantic": verifies the problem was actually solved
      - "syntax": verifies code still compiles
      - "cannot_verify": honest admission — no semantic test possible for this fix type
    """
    rule_name = fix.get("rule", "")
    fix_file = fix.get("file", fix.get("finding_file", ""))
    commands = []

    # ── 伪修复 (tag-*): 诚实声明无法语义验证 ──
    if rule_name.startswith("tag-"):
        fp = root / fix_file
        if fp.exists():
            # v4 honesty: tag-* rules only add comments. We CANNOT verify
            # that the problem was solved — only that the tag was added.
            # This is NOT a valid semantic verification.
            commands.append(
                (f"[CANNOT_SEMANTICALLY_VERIFY] {rule_name} 仅添加注释——无法验证问题是否被解决",
                 f'echo "CANNOT_VERIFY: {rule_name} is a comment-only fix. '
                 f'Semantic verification requires a real code change."',
                 "cannot_verify")
            )
            # Syntax check is still useful
            commands.append(
                (f"[syntax] {fix_file}",
                 f'"{sys.executable}" -c "import ast; ast.parse(open(r\'{fp}\', encoding=\'utf-8\').read()); print(\'OK\')"',
                 "syntax")
            )

    # ── 真修复: UTF-8 头 —— 验证 stdout 重配置实际存在 ──
    elif "utf8" in rule_name.lower() and "add" in rule_name.lower():
        fp = root / fix_file
        if fp.exists():
            # Semantic: verify the actual stdout reconfiguration is present
            commands.append(
                (f"[semantic] 确认 sys.stdout.reconfigure 存在: {fix_file}",
                 f'"{sys.executable}" -c "'
                 f'content = open(r\'{fp}\', encoding=\'utf-8\').read(); '
                 f'has_reconf = \'sys.stdout.reconfigure\' in content; '
                 f'has_import = \'import sys\' in content; '
                 f'print(f\'stdout={has_reconf} import={has_import}\'); '
                 f'exit(0 if has_reconf else 1)"',
                 "semantic")
            )
            # Syntax
            commands.append(
                (f"[syntax] {fix_file}",
                 f'"{sys.executable}" -c "compile(open(r\'{fp}\', encoding=\'utf-8\').read(), r\'{fp}\', \'exec\'); print(\'OK\')"',
                 "syntax")
            )

    # ── 真修复: grep -P → grep -E —— 验证 PCRE 语法不再出现 ──
    elif "grep" in rule_name.lower() and "pcre" in rule_name.lower():
        fp = root / fix_file
        if fp.exists():
            # Semantic: verify no grep -P remains in file
            commands.append(
                (f"[semantic] 确认 grep -P 已移除: {fix_file}",
                 f'python -c "'
                 f'import sys; '
                 f'content = open(r\'{fp}\', encoding=\'utf-8\').read(); '
                 f'found = \'grep -P\' in content or \'grep -nP\' in content; '
                 f'print(f\'grep-P-remaining={found}\'); '
                 f'sys.exit(1 if found else 0)"',
                 "semantic")
            )

    # ── 生成规则修复: metric-dead-value —— 验证 JSONL 行格式不变 ──
    elif "metric" in rule_name.lower() and "dead" in rule_name.lower():
        fp = root / fix_file
        if fp.exists():
            commands.append(
                (f"[syntax] JSONL 仍有效: {fix_file}",
                 f'python -c "'
                 f'import json; '
                 f'lines = open(r\'{fp}\', encoding=\'utf-8\').read().strip().split(chr(10)); '
                 f'ok = sum(1 for l in lines if l and json.loads(l)); '
                 f'print(f\'valid-lines={ok}\')"',
                 "syntax")
            )

    # ── 通用 .py 文件修复: 语法检查 + 检查原始 pattern 是否仍存在于文件中 ──
    elif fix_file.endswith(".py"):
        fp = root / fix_file
        if fp.exists():
            pattern = fix.get("pattern", "")
            match_text = fix.get("match", "")
            # Syntax check
            commands.append(
                (f"[syntax] {fix_file}",
                 f'"{sys.executable}" -c "import ast; ast.parse(open(r\'{fp}\', encoding=\'utf-8\').read()); print(\'OK\')"',
                 "syntax")
            )
            # Semantic: if we know the original match text, verify it's no longer present
            # (for genuine fixes that remove/replace code, not tag-*)
            if match_text and not rule_name.startswith("tag-"):
                escaped = match_text.replace("'", "\\'").replace('"', '\\"')
                commands.append(
                    (f"[semantic] 确认原始匹配不再存在: {fix_file}",
                     f'python -c "'
                     f'content = open(r\'{fp}\', encoding=\'utf-8\').read(); '
                     f'found = \'{escaped}\' in content; '
                     f'print(f\'original-match-remaining={{found}}\'); '
                     f'exit(0 if not found else 1)"',
                     "semantic")
                )

    # ── 兜底: 重新扫描 skill —— 检查问题数是否减少 ──
    if not commands:
        skill_name = root.name
        commands.append(
            (f"[semantic] 重新扫描: {skill_name}",
             f'"{sys.executable}" -c "from engine.system1 import scanner; '
             f'r = scanner.scan_skill(\'{skill_name}\'); '
             f'print(f\'findings={{r.get(\\\'findings_count\\\', \\\'?\\\')}}\')"',
             "semantic")
        )

    return commands


# ── (DGM) Archive snapshot ────────────────────────────────────────────────────

def archive_if_diverse(skill_name: str, round_num: int) -> Optional[str]:
    """(DGM) Create a diversity-preserving archive snapshot.

    Only archives if the current behavior differs from the last archived version.
    This prevents the archive from filling with near-identical snapshots.
    """
    params = _load_params(skill_name)
    archive_interval = params.get("archive_interval", 10)

    if round_num % archive_interval != 0:
        return None

    root = _find_skill_root(skill_name)
    if not root:
        return None

    archive_dir = root / "data" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Check if we should archive by comparing with last archive
    archives = sorted(archive_dir.glob("archive-*"), reverse=True)
    if archives:
        last_archive = archives[0]
        # Compare findings patterns
        last_meta_path = last_archive / "meta.json"
        if last_meta_path.exists():
            try:
                last_meta = json.loads(last_meta_path.read_text(encoding="utf-8"))
                last_patterns = set(last_meta.get("findings_patterns", []))
                # Get current patterns
                current_scan = scanner.scan_skill(skill_name)
                current_patterns = set(
                    f.get("pattern", "") for f in current_scan.get("findings", [])
                )
                # If patterns are identical, skip archive
                if last_patterns == current_patterns:
                    return None
            except (json.JSONDecodeError, OSError):
                pass  # TODO: log or re-raise

    # Create archive
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_id = f"archive-{ts}"
    archive_path = archive_dir / archive_id
    archive_path.mkdir(parents=True, exist_ok=True)

    # Copy engine/ and references/
    for src_dir in ["engine", "references", "skills"]:
        src = root / src_dir
        if src.exists():
            import shutil
            shutil.copytree(src, archive_path / src_dir, dirs_exist_ok=True)

    # Write archive metadata
    current_scan = scanner.scan_skill(skill_name)
    meta = {
        "archive_id": archive_id,
        "iso_created": datetime.now(timezone.utc).isoformat(),
        "round": round_num,
        "findings_count": current_scan.get("findings_count", 0),
        "findings_patterns": list(set(
            f.get("pattern", "") for f in current_scan.get("findings", [])
        )),
        "gvu_snr": memory.calculate_gvu_snr(skill_name)
    }
    (archive_path / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    memory.write_event(skill_name, "integrate", {
        "type": "archive",
        "archive_id": archive_id,
        "round": round_num
    })

    return archive_id


# ── Internal helpers ──────────────────────────────────────────────────────────

def _update_known_issues(skill_name: str, fix_report: dict):
    """Append fix records to known-issues.md."""
    root = _find_skill_root(skill_name)
    ki_path = root / "references" / "known-issues.md"
    if not ki_path.exists():
        return

    entries = []
    for f in fix_report.get("fixes", []):
        if f.get("applied"):
            entries.append(
                f"\n<!-- @ttl version_added=\"1.0\" "
                f"last_referenced_iso=\"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\" "
                f"reference_count=\"0\" -->\n"
                f"### [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] "
                f"自动修复: {f.get('rule', '?')}\n"
                f"- **文件:** {f.get('file', f.get('finding_file', '?'))}\n"
                f"- **动作:** {f.get('action', f.get('reason', '?'))}\n"
            )

    if entries:
        current = ki_path.read_text(encoding="utf-8")
        ki_path.write_text(current + "\n".join(entries), encoding="utf-8")


# ── Impasse detection ─────────────────────────────────────────────────────────

def detect_impasse(skill_name: str, round_num: int, params: dict) -> dict:
    """Detect if System 1 is stuck — should System 2 engage?

    Impasse types (inspired by SOAR + GEPA trace analysis):
    - tie: same findings keep appearing across rounds
    - conflict: fix A introduces bug B
    - rejection: findings exist but no rule matches
    - no-change: no findings but system still has known weaknesses
    - trace_failure: GEPA execution traces show fixes failing to execute
    """
    events = memory.read_events(skill_name, limit=params.get("meta_audit_interval", 5) * 3)

    # Check for "rejection" impasse: findings that can't be fixed
    recent_fix_events = [e for e in events if e["event_type"] == "fix"]
    recent_scan_events = [e for e in events if e["event_type"] == "scan"]

    system2_needed = False
    reasons = []

    # Impasse: same pattern flagged 3+ rounds in a row without being fixed
    pattern_history = []
    for e in recent_scan_events:
        findings = e.get("data", {}).get("findings", [])
        patterns_seen = set(f.get("pattern", "") for f in findings)
        pattern_history.append(patterns_seen)

    if len(pattern_history) >= 3:
        stubborn = pattern_history[0]
        for ph in pattern_history[1:]:
            stubborn = stubborn & ph
        if stubborn:
            reasons.append(f"僵局（顽固模式）: {stubborn} 连续 {len(pattern_history)} 轮出现")
            system2_needed = True

    # Impasse: no fix rules available for current findings
    last_scan = recent_scan_events[0] if recent_scan_events else None
    if last_scan:
        findings = last_scan.get("data", {}).get("findings", [])
        unruly = [f for f in findings if f.get("pattern") not in {
            "missing-utf8-header", "grep-pcre", "hardcoded-versions",
            "language-drift", "hardcoded-numbers", "missing-docstring",
            "bare-except", "except-pass", "skill-md-oversize",
        }]
        if unruly and not recent_fix_events:
            reasons.append(f"无规则匹配: {len(unruly)} 个发现无法自动修复")
            system2_needed = True

    # (GEPA) Impasse: execution traces show fix failures
    recent_traces = [e for e in events if e["event_type"] == "execution_trace"]
    failed_traces = [t for t in recent_traces[:10]
                     if not t.get("data", {}).get("success", True)]
    if len(failed_traces) >= 3:
        failed_rules = set(t.get("data", {}).get("fix_rule", "?")
                          for t in failed_traces)
        reasons.append(f"GEPA 执行验证: {len(failed_traces)} 个修复的运行时测试失败 "
                       f"(规则: {failed_rules})")
        system2_needed = True

    # (GVU) Impasse: verifier SNR below threshold
    gvu = memory.calculate_gvu_snr(skill_name)
    if not gvu["stable"] and gvu["details"]["total_fixes"] > 5:
        reasons.append(f"GVU 不稳定: {gvu['verdict']}")
        system2_needed = True

    # (P3) Impasse: independent verification disagreements stacking up
    recent_traces_all = [e for e in events if e["event_type"] == "execution_trace"]
    indep_disagreements = [
        t for t in recent_traces_all[:15]
        if t.get("data", {}).get("phase") == "independent_verification"
        and t.get("data", {}).get("verdict") == "disagreement"
    ]
    if len(indep_disagreements) >= 2:
        reasons.append(f"独立验证分歧: {len(indep_disagreements)} 次独立验证与自我验证不一致——"
                       f"修复策略可能需要审视")
        system2_needed = True

    return {
        "impasse_detected": system2_needed,
        "reasons": reasons,
        "round": round_num,
        "should_engage_system2": system2_needed
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def _reflect_and_extend_scanner(skill_name: str, rounds: list,
                                  max_rounds: int, current_round: int) -> tuple:
    """(v1.2 自进化回路) 收敛后不停止——运行 Reflect，将观测转化为新 scanner 规则。

    这是"自指闭环"的关键连接：
      Reflect 分析执行历史 → 提取新模式 → rule_generator 创建 scanner 规则
      → 重新扫描 → 如果发现新问题 → 继续进化 → 否则真正收敛

    Returns:
      (extended: bool, new_findings_count: int)
      extended=True means new findings found — loop should continue
    """
    print(f"\n  {'─'*40}")
    print(f"  [EVOLVE] Reflect → Generate → Rescan")
    print(f"  {'─'*40}")

    generations = 0
    new_rules = []

    # 1. Run Layer 2 Reflect: analyze execution history for undiscovered patterns
    try:
        from engine import reflect
        print(f"  [EVOLVE:Reflect] 分析执行历史...")
        reflect_result = reflect.run_reflect(skill_name, lookback=50, cross_skill=True)
        obs = reflect_result.get("observation", {})

        print(f"    顽固模式: {len(obs.get('stubborn_patterns', {}))}")
        print(f"    GEPA执行失败: {obs.get('failed_traces', 0)}/{obs.get('execution_traces', 0)}")
        if obs.get('trace_failures_by_rule'):
            print(f"    失败规则: {obs['trace_failures_by_rule']}")

    except ImportError:
        print(f"  [EVOLVE] Reflect 不可用——跳过")
        return {"extended": False, "new_findings": 0,
                "observer_veto": False, "veto_reasons": []}

    # 2. Source: Cross-skill patterns from HyperAgents scan
    cross = reflect_result.get("cross_skill_scan", {})
    shared = cross.get("shared_patterns", {}) if isinstance(cross, dict) else {}
    if shared:
        print(f"  [EVOLVE:HyperAgents] 跨技能共享模式: {list(shared.keys())}")
        # Enrich with scanner rules — patterns seen in 3+ skills
        for pattern_name in shared:
            if _pattern_already_covered(skill_name, pattern_name):
                continue
            try:
                from engine.system2 import rule_generator as rg
                rule = rg.generate_rule_from_stubborn_pattern(
                    skill_name, pattern_name, shared[pattern_name],
                    affected_files=[f"skills/*/{pattern_name}"],
                    related_traces=[],
                    deliberation_text=f"HyperAgents: {shared[pattern_name]}个技能共享此模式",
                    known_fix_patterns={"strategy": "cross-skill-fix"}
                )
                if rule:
                    new_rules.append(rule)
                    generations += 1
                    print(f"    → 生成规则: {rule['name']} (置信度 {rule['source']['confidence']:.0%})")
            except ImportError as _ie:
                _warn_import(skill_name, "optional_module", str(_ie))

    # 3. Source: GEPA execution trace failures → generate fix-improvement rules
    trace_failures = obs.get("trace_failures_by_rule", {})
    for rule_name, failure_count in trace_failures.items():
        if failure_count >= 2:
            # A rule that consistently produces broken fixes needs an improved version
            pattern_tag = f"gepa-failed-{rule_name}"
            if _pattern_already_covered(skill_name, pattern_tag):
                continue
            try:
                from engine.system2 import rule_generator as rg
                rule = rg.generate_rule_from_stubborn_pattern(
                    skill_name, pattern_tag, failure_count,
                    affected_files=["engine/system1/rules.py"],
                    related_traces=[],
                    deliberation_text=f"GEPA: {rule_name} 规则 {failure_count}次执行后验证失败",
                    known_fix_patterns={"strategy": "gepa-verification-enhance"}
                )
                if rule:
                    # Derive regex: scan for the failing fix function definition
                    # Extract the base name (remove tag-/fix- prefix)
                    base = rule_name.replace('tag-', '').replace('fix-', '').replace('gepa-failed-', '')
                    rule["pattern_regex"] = r'def\s+fix_' + base.replace('-', r'[-_]')
                    rule["file_glob"] = "engine/system1/rules.py"
                    rg.save_rule(skill_name, rule)
                    new_rules.append(rule)
                    generations += 1
                    print(f"    → GEPA规则: {rule['name']} (失败={failure_count}次, regex={rule['pattern_regex'][:40]})")
            except ImportError as _ie:
                _warn_import(skill_name, "optional_module", str(_ie))

    # 4. Source: Reflect proposals that passed verification
    validation = reflect_result.get("validation", {})
    if validation.get("verified", 0) > 0:
        for result in validation.get("results", []):
            if result.get("verdict") == "verified":
                from engine.system2 import proposer as prop
                proposal = prop.load_proposal(skill_name, result["proposal_id"])
                if proposal and proposal.get("change_type") == "patterns":
                    # Architecture proposals about patterns — create a scanner rule
                    pattern_tag = f"proposal-{proposal['proposal_id'][:20]}"
                    if not _pattern_already_covered(skill_name, pattern_tag):
                        try:
                            from engine.system2 import rule_generator as rg
                            rule = rg.generate_rule_from_stubborn_pattern(
                                skill_name, pattern_tag, 1,
                                affected_files=proposal.get("files_changed", ["engine/*.py"]),
                                related_traces=[],
                                deliberation_text=proposal.get("description", ""),
                                known_fix_patterns={"strategy": "reflect-proposal"}
                            )
                            if rule:
                                new_rules.append(rule)
                                generations += 1
                                print(f"    → 生成规则: {rule['name']} (已通过宪法验证)")
                        except ImportError as _ie:
                            _warn_import(skill_name, optional_module, str(_ie))

    if generations == 0:
        print(f"  [EVOLVE] 未发现可生成的新模式")
        # (v1.3) Observer veto: check for critical unfixable issues
        veto_reasons = _collect_observer_veto_reasons(reflect_result, obs)
        if veto_reasons:
            print(f"  [OBSERVER-VETO] 发现 {len(veto_reasons)} 个需人工介入的问题:")
            for r in veto_reasons[:3]:
                print(f"    ⚠️  {r['reason'][:120]}")
            return {"extended": False, "new_findings": 0,
                    "observer_veto": True, "veto_reasons": veto_reasons}
        return {"extended": False, "new_findings": 0,
                "observer_veto": False, "veto_reasons": []}

    print(f"  [EVOLVE] 共生成 {generations} 个新规则 → 进入试用期")

    # ── (v1.3 自进化闭合) 立即为缺少代码的规则生成 fix 函数 ──
    # 之前 codegen 只在 loop 结束时运行，导致规则创建后要等下个 session
    # 才有可执行代码。现在同 session 内闭合。
    try:
        from engine.system2 import code_generator as cg
        cg_result = cg.try_generate_for_pending_rules(skill_name)
        if cg_result.get("generated", 0) > 0:
            print(f"  [EVOLVE:CodeGen] {cg_result['verdict']}")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # 5. Re-scan with new rules
    from engine.system1 import scanner
    rescan = scanner.scan_skill(skill_name)
    new_findings = rescan.get("findings_count", 0)

    if new_findings > 0:
        print(f"  [EVOLVE:Rescan] 发现 {new_findings} 个新问题!")
        # Show what was found
        for f in rescan.get("findings", [])[:5]:
            print(f"    [{f.get('severity','?')}] {f.get('file','?')}:{f.get('line','?')} — {f.get('description','')[:80]}")
        if new_findings > 5:
            print(f"    ... 还有 {new_findings - 5} 条")
        # (v1.3) Check for veto even when extended
        veto_reasons = _collect_observer_veto_reasons(reflect_result, obs)
        return {"extended": True, "new_findings": new_findings,
                "observer_veto": bool(veto_reasons), "veto_reasons": veto_reasons}
    else:
        print(f"  [EVOLVE:Rescan] 新规则未匹配到新问题")
        veto_reasons = _collect_observer_veto_reasons(reflect_result, obs)
        return {"extended": False, "new_findings": 0,
                "observer_veto": bool(veto_reasons), "veto_reasons": veto_reasons}


def _collect_observer_veto_reasons(reflect_result: dict, obs: dict) -> list:
    """(v1.3) Collect critical issues from Observer that cannot be auto-fixed.

    These are issues the Observer detected but the rule_generator cannot create
    scanner rules for. They require human intervention.

    Returns list of {reason, severity, category} dicts. Empty list = no veto.
    """
    reasons = []

    # 1. Constitutional violations detected by Observer
    questions = reflect_result.get("questions", {}).get("questions", [])
    for q in questions:
        if q.get("category") == "constitutional_violation":
            reasons.append({
                "reason": f"宪法违规: {q['question'][:200]}",
                "severity": "critical",
                "category": "constitutional"
            })
        elif q.get("category") == "constitution_realizability":
            # Honest gaps that can't be closed automatically
            reasons.append({
                "reason": f"宪法可实现性缺口: {q['question'][:200]}",
                "severity": "high",
                "category": "constitutional"
            })

    # 2. Critical evidence degradation
    evidence_rate = obs.get("evidence_pass_rate", 1.0)
    if evidence_rate < 0.3:
        reasons.append({
            "reason": f"证据通过率极低 ({evidence_rate:.0%})——修复质量严重下降",
            "severity": "critical",
            "category": "evidence_degradation"
        })

    # 3. Persistent trace failures (>3 for same rule)
    trace_failures = obs.get("trace_failures_by_rule", {})
    for rule, count in trace_failures.items():
        if count >= 3:
            reasons.append({
                "reason": f"规则 '{rule}' 执行后验证失败 {count} 次——修复不可靠",
                "severity": "high",
                "category": "persistent_failure"
            })

    # 4. Stubborn patterns that couldn't be turned into rules
    stubborn = obs.get("stubborn_patterns", {})
    unknown_stubborn = {
        p: c for p, c in stubborn.items()
        if p not in ("language-drift", "hardcoded-numbers", "hardcoded-versions",
                     "grep-pcre", "missing-utf8-header", "self-reference",
                     "missing-docstring", "dev-null")
        and c >= 3
    }
    if unknown_stubborn:
        reasons.append({
            "reason": f"顽固模式无法自动修复: {list(unknown_stubborn.keys())} (重复 {max(unknown_stubborn.values())} 次)",
            "severity": "high",
            "category": "stubborn_unknown"
        })

    return reasons


def _pattern_already_covered(skill_name: str, pattern_name: str) -> bool:
    """Check if a pattern is FULLY covered: detected AND has an auto-fix rule.

    "Fully covered" means:
      1. SCAN_PATTERNS detects it (regex exists)
      2. A fix rule exists (SEED_RULES or generated+active) with a fix function
         or a generated rule with _generated_code_path

    If a pattern is detected but has NO fix function → NOT fully covered →
    should trigger rule generation to fill the gap. This is the key to closing
    the self-evolution loop.
    """
    from engine.system1.scanner import SCAN_PATTERNS
    from engine.system1 import rules as rules_mod
    from engine.system2 import rule_generator as rg

    # ── Check detection coverage ──
    detected = False
    for entry in SCAN_PATTERNS:
        if entry[0] == pattern_name:
            detected = True
            break

    if not detected:
        return False  # Not even detected — definitely not covered

    # ── Check fix coverage in SEED_RULES ──
    for rule_entry in rules_mod.SEED_RULES:
        if rule_entry.get("pattern") == pattern_name:
            if rule_entry.get("fix") is not None:
                return True  # Has a fix function in SEED_RULES

    # ── Check fix coverage in generated rules ──
    gen_rules = rg.load_all_generated_rules(skill_name)
    for r in gen_rules:
        if r.get("status") == "deprecated":
            continue
        if r.get("pattern") == pattern_name or r.get("name") == pattern_name:
            # Has _generated_code_path or has fix_strategy (will get code soon)
            if r.get("_generated_code_path") or r.get("fix_strategy"):
                return True

    # Detected but no fix function exists → NOT fully covered
    return False


def run_loop(skill_name: str, max_rounds: Optional[int] = None,
             stop_rounds: Optional[int] = None,
             layer1_only: bool = False) -> dict:
    """Run the full Layer 1 improvement loop until convergence.

    This is the universal self-improvement engine. It works identically
    on any skill, including skill-builder-v3 itself.

    Loop sequence (v1.2):
      CHECKPOINT(RESUME?) → GENERATE → EVALUATE → SELECT → INTEGRATE
        → INDEPENDENT-VERIFY(P3) → EVIDENCE-CHECK(P2) → CHECKPOINT(SAVE)
        → ARCHIVE(DGM) → HILLCLIMB(P9)

    v1.2 additions:
      - P1 (Gödel Agent): generated rules from rule_generator.py are merged into RULES
      - P2 (EVE-Agent): evidence chain validation — every fix carries evidence_span
      - P3 (Ultragoal): verify_execution_independent() — fresh-eyes subprocess verifier
      - P5 (Claude Code): hooks_bridge.py — auto-trigger via SessionStart/Stop/PostFailure
      - P6 (HGM): metaproductivity — descendant quality scoring for all rules
      - P8 (Yunjue): parallel.py — N candidates in isolated worktrees
      - P9 (Karpathy): hillclimb.py — unified fitness auto-tuning
      - P10 (Claude Code): checkpoint.py — crash recovery from any round

    Returns a structured report.
    """
    params = _load_params(skill_name)
    stop_rounds = stop_rounds if stop_rounds is not None else params.get("stop_rounds", 2)
    max_rounds = max_rounds if max_rounds is not None else params.get("max_iterations", 10)

    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在"}

    # GVU pre-check: if system is unstable and in trusted mode, require human
    gvu = memory.calculate_gvu_snr(skill_name)
    if not gvu["stable"] and params.get("autonomy_level") == "trusted":
        print(f"  [GVU 门控] {gvu['verdict']}")
        print(f"  → 系统不稳定，trusted 模式下需要人工确认才能继续")
        memory.write_event(skill_name, "loop", {
            "phase": "gated",
            "reason": "gvu_unstable",
            "gvu_report": gvu
        })
        return {
            "skill": skill_name,
            "total_rounds": 0,
            "converged": False,
            "total_findings": 0,
            "total_fixes": 0,
            "gvu_stability": gvu,
            "gated": True,
            "reason": "GVU 门控拒绝——验证器 SNR 低于阈值，系统不稳定，需人工校准后重试"
        }

    # Log loop start
    memory.write_event(skill_name, "loop", {"phase": "start", "params": params})

    # (P10) Checkpoint: detect and resume from incomplete loop
    try:
        from engine import checkpoint as ckpt
        resume = ckpt.resume_from_checkpoint(skill_name)
        if resume.get("resumed"):
            print(f"  [CHECKPOINT-RESUME] 从 round {resume['round_num']} 恢复")
            for action in resume.get("actions", []):
                print(f"    {action}")
            fix_fingerprints = resume.get("fix_fingerprints", [])
            consecutive_zero = resume.get("consecutive_zero", 0)
            start_round = resume.get("round_num", 1)
            findings_count = resume.get("findings_count", 0)  # v1.2: 从快照恢复
            skip_to = resume.get("round_num", 0)
            # (v1.3) Restore suspension state from checkpoint
            auto_fix_suspended = resume.get("auto_fix_suspended", False)
            suspension_reason = resume.get("suspension_reason", "")
        else:
            fix_fingerprints = []
            consecutive_zero = 0
            skip_to = 0
            findings_count = 0  # v1.2: 初始化，避免首轮 checkpoint 写入 -1
            # (v1.3) Observer veto state initialization
            auto_fix_suspended = False
            suspension_reason = ""
    except ImportError:
        fix_fingerprints = []
        consecutive_zero = 0
        skip_to = 0
        findings_count = 0  # v1.2: 初始化，避免首轮 checkpoint 写入 -1
        # (v1.3) Observer veto state — 当 Observer 判定需要人工介入时阻断自动修复
        auto_fix_suspended = False
        suspension_reason = ""

    rounds = []

    for round_num in range(max(1, skip_to), max_rounds + 1):
        # (P10) Save checkpoint BEFORE each round
        try:
            from engine import checkpoint as ckpt
            ckpt.checkpoint_wrapper(skill_name, round_num, {
                "findings_count": findings_count,  # v1.2: 已在循环前初始化，不再用 -1
                "consecutive_zero": consecutive_zero,
                "fix_fingerprints": fix_fingerprints,
                "total_fixes_applied": sum(r.get("fixes_applied", 0) for r in rounds)
            })
        except ImportError as _ie:
            _warn_import(skill_name, "optional_module", str(_ie))

        print(f"\n{'─'*50}")
        print(f"  Round {round_num}/{max_rounds} | Target: {skill_name}")
        print(f"{'─'*50}")

        t0 = time.time()

        # 1. GENERATE
        gen = generate_fixes(skill_name)
        findings = gen["findings"]
        findings_count = gen["findings_count"]
        print(f"  [GENERATE] {findings_count} findings "
              f"(scan={gen['source']['scan']} patterns={gen['source']['patterns']})")

        # 2. EVALUATE
        ev = evaluate_fixes(skill_name, findings)
        print(f"  [EVALUATE] auto={ev['auto_fixable']} system2={ev['needs_system2']}")

        # TAPO: Check for low-quality rules
        for rname, q in ev.get("rule_quality", {}).items():
            if q.get("last_10_rate", 1.0) < 0.5 and q.get("total", 0) >= 5:
                print(f"  [TAPO] 规则 '{rname}' 近期成功率仅 {q['last_10_rate']:.0%}")

        # Check impasse
        impasse = detect_impasse(skill_name, round_num, params)
        if impasse["impasse_detected"] and not layer1_only:
            print(f"  [!!] 僵局检测: {'; '.join(impasse['reasons'])}")
            print(f"  → 触发 System 2 深度分析...")
            _trigger_system2(skill_name, findings, impasse)

        # (v1.3) Observer veto: 暂停状态下跳过自动修复，仅扫描
        if auto_fix_suspended:
            print(f"  [OBSERVER-VETO] 自动修复已暂停: {suspension_reason[:100]}")
            print(f"  [OBSERVER-VETO] 本轮仅扫描——不执行修复。需人工审查后重新运行。")

        # 3. SELECT
        sel = [] if auto_fix_suspended else select_fixes(skill_name, ev)
        print(f"  [SELECT] {len(sel)} fixes selected")

        # 4. INTEGRATE
        int_result = integrate_fixes(skill_name, sel, findings)
        if "error" in int_result:
            print(f"  [!!] INTEGRATE 失败: {int_result['error']}")
            memory.write_event(skill_name, "integrate",
                               {"status": "failed", "error": int_result["error"]})

            # TAPO: Mark fixes that led to rollback
            for fp_entry in fix_fingerprints[-3:]:
                memory.write_fix_quality(skill_name, fp_entry["rule"],
                                         False,
                                         {"rollback_correlated": True,
                                          "rollback_round": round_num})
        else:
            print(f"  [INTEGRATE] applied={int_result.get('fixes_applied', 0)} "
                  f"snapshot={int_result.get('snapshot_id', '?')}")

            # 4.5 (P3) INDEPENDENT VERIFY — fresh-eyes verifier + evidence chain
            indep_result = verify_execution_independent(skill_name, int_result)
            icon = "[OK]" if indep_result.get("agreement") else "[!!]"
            print(f"  [INDEP-VERIFY] {icon} self={'OK' if indep_result.get('self_verified') else 'FAIL'}"
                  f" | independent={indep_result.get('independent_verdict', '?')}")
            print(f"  [EVIDENCE] 证据通过率: {indep_result.get('evidence_pass_rate', 0):.0%}")

            # P3: Escalate to System 2 if independent disagrees with self
            if indep_result.get("should_escalate") and not layer1_only:
                print(f"  [!!] 独立验证分歧——触发 System 2 深度审查...")
                _trigger_system2(skill_name, findings, impasse)

            # (S5: AskGate) Calibration check — is agent overconfident?
            try:
                from engine import ask_gate
                gate = ask_gate.check_calibration(skill_name, indep_result)
                if gate.get("should_pause"):
                    print(f"  [S5:AskGate] {gate['verdict']}")
                    print(f"    校准: self_confidence={gate['self_confidence']:.0%} "
                          f"actual_accuracy={gate['actual_accuracy']:.0%} "
                          f"gap={gate['confidence_gap']:.0%}")
                    memory.write_event(skill_name, "loop", {
                        "phase": "ask_gate_paused",
                        "reason": gate.get("verdict", "overconfidence detected"),
                        "confidence_gap": gate.get("confidence_gap", 0)
                    })
                    if gate.get("escalate_to_human"):
                        print(f"  [S5:AskGate] → 暂停自改进, 等待人工校准")
                        # (v1.3) Observer veto: 真正的阻断——不只是建议
                        auto_fix_suspended = True
                        suspension_reason = (
                            f"AskGate escalation: self_confidence={gate['self_confidence']:.0%} "
                            f"actual_accuracy={gate['actual_accuracy']:.0%} "
                            f"gap={gate['confidence_gap']:.0%}"
                        )
                        memory.write_event(skill_name, "loop", {
                            "phase": "human_escalation",
                            "reason": suspension_reason,
                            "gate_data": gate
                        })
                        # 本轮已经执行了修复，但标记后续轮暂停
                        print(f"  [S5:AskGate] → 后续轮次暂停自动修复，仅扫描不修改")
            except ImportError as _ie:
                _warn_import(skill_name, "optional_module", str(_ie))

            # Use the independent result as the verification outcome
            verify_result = {
                "verified": indep_result.get("agreement", False),
                "verdict": indep_result.get("independent_verdict", "?"),
                "evidence_pass_rate": indep_result.get("evidence_pass_rate", 0.0)}
            print(f"  [GEPA-VERIFY] {icon} {verify_result.get('verdict', '?')}")

            # 4.6 (DGM) ARCHIVE if diverse
            archive_id = archive_if_diverse(skill_name, round_num)
            if archive_id:
                print(f"  [DGM-ARCHIVE] {archive_id}")

            # TAPO: Record fix fingerprints for later rollback correlation
            prev_fix_count = len(fix_fingerprints)
            for fix in int_result.get("fix_report", {}).get("fixes", []):
                if fix.get("applied"):
                    fix_fingerprints.append({
                        "round": round_num,
                        "rule": fix.get("rule", "?"),
                        "file": fix.get("file", fix.get("finding_file", "?")),
                        "snapshot": int_result.get("snapshot_id", "?"),
                        # (S1: GDPO) attach gdpo score if available
                        "gdpo": indep_result.get("gdpo_score", {})
                    })

            # (S2: TextGrad) On fix failure, generate textual gradient
            if not indep_result.get("agreement") and indep_result.get("evidence_pass_rate", 1.0) < 0.6:
                try:
                    from engine import evidence as ev
                    for fix in int_result.get("fix_report", {}).get("fixes", []):
                        if fix.get("applied"):
                            grad = ev.generate_textual_gradient(
                                fix_rule=fix.get("rule", "?"),
                                fix_file=fix.get("file", fix.get("finding_file", "?")),
                                evidence_span=ev.EvidenceSpan(
                                    evidence_type="run_test",
                                    command="(来自独立验证)",
                                    expected="独立验证通过",
                                    actual=indep_result.get("independent_verdict", "失败"),
                                    source_file=fix.get("file", fix.get("finding_file", "?"))
                                )
                            )
                            memory.write_event(skill_name, "execution_trace", {
                                "phase": "textual_gradient",
                                "gradient": grad
                            })
                            print(f"  [S2:TextGrad] {grad.get('failure_mode', '?')[:100]}")
                except ImportError as _ie:
                    _warn_import(skill_name, "optional_module", str(_ie))
            # (P6/HGM) Record descendant links between fixes in this round.
            # ⚠️  v4 诚实声明: 以下代码将同一轮内任意两个修复标记为关联——
            # 仅因时间相邻，无独立性检查，无混淆变量排除。
            # 这是"相关非因果"的代码实现 (cum hoc ergo propter hoc)。
            # 真因果关系需要: 子修复读取了父修复改变的状态/文件。
            # 当前实现作为启发式信号使用——不应用于自动决策。
            new_fixes = fix_fingerprints[prev_fix_count:]
            if len(new_fixes) >= 2:
                for i, parent in enumerate(new_fixes[:-1]):
                    for child in new_fixes[i+1:]:
                        try:
                            memory.record_descendant_link(
                                skill_name, parent["rule"], child["rule"],
                                relationship="correlated",  # v4: was "enabled" — 伪因果修正
                                metadata={"round": round_num, "_caveat": "temporal adjacency, not proven causation"}
                            )
                        except AttributeError:
                            pass  # P6 not loaded yet

        duration = round(time.time() - t0, 2)

        # Track convergence
        if findings_count == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0

        # (B: Blindspot) True convergence: zero findings AND blind spot audit fresh
        converged = False
        if consecutive_zero >= stop_rounds:
            try:
                from engine import blindspot as bs
                conv_check = bs.check_true_convergence(
                    skill_name, findings_count, consecutive_zero, stop_rounds
                )
                if conv_check.get("should_audit"):
                    print(f"  [B:盲区] {conv_check['reason']}")
                    print(f"  → 运行盲区审计...")
                    audit_result = bs.run_blindspot_audit(skill_name)
                    print(f"  [B:盲区] {audit_result['verdict']}")
                converged = conv_check.get("truly_converged", False)
            except ImportError:
                converged = True  # Fallback: standard convergence
        else:
            converged = False

        # (S10: Anti-Gaming) Before declaring convergence, check for gaming
        if converged:
            try:
                from engine import anti_gaming
                gaming = anti_gaming.run_anti_gaming_check(skill_name)
                if not gaming.get("convergence_trustworthy", True):
                    print(f"  [S10:AntiGaming] {gaming['verdict']}")
                    print(f"    风险={gaming['risk_level']} —— 收敛可能为假")
                    converged = False  # Override — don't stop if gaming detected
            except ImportError as _ie:
                _warn_import(skill_name, "optional_module", str(_ie))

        round_data = {
            "round": round_num,
            "findings_count": findings_count,
            "consecutive_zero": consecutive_zero,
            "converged": converged,
            "duration_seconds": duration,
            "impasse": impasse["impasse_detected"],
            "fixes_applied": int_result.get("fixes_applied", 0) if "error" not in int_result else 0,
            "execution_verified": verify_result.get("verified", False) if "error" not in int_result else False
        }
        rounds.append(round_data)

        # ── (v1.3) Periodic Observer trigger: 不等收敛，主动监控 ──
        observer_interval = params.get("observer_interval", 5)
        if round_num > 1 and round_num % observer_interval == 0 and not converged:
            print(f"\n  [OBSERVER] 定时触发 (每 {observer_interval} 轮)")
            try:
                from engine import reflect
                mini_obs = reflect.meta_observe(skill_name, lookback=30)
                stubborn = mini_obs.get("stubborn_patterns", {})
                trace_fails = mini_obs.get("trace_failures_by_rule", {})
                failed_traces = mini_obs.get("failed_traces", 0)
                print(f"    顽固模式: {len(stubborn)} | 执行失败: {failed_traces}/{mini_obs.get('execution_traces', 0)}")

                # Lightweight escalation check
                escalated = False
                if trace_fails:
                    persistent = {r: c for r, c in trace_fails.items() if c >= 3}
                    if persistent:
                        print(f"    [!!] 持续执行失败: {persistent}")
                        escalated = True

                unknown_stubborn = {
                    p: c for p, c in stubborn.items()
                    if p not in ("language-drift", "hardcoded-numbers", "hardcoded-versions",
                                 "grep-pcre", "missing-utf8-header", "self-reference",
                                 "missing-docstring", "dev-null") and c >= 3
                }
                if unknown_stubborn:
                    print(f"    [!!] 顽固未知模式: {list(unknown_stubborn.keys())}")
                    escalated = True

                if mini_obs.get("rollbacks", 0) >= 2:
                    print(f"    [!!] 回滚过多: {mini_obs['rollbacks']} 次")
                    escalated = True

                if escalated and round_num >= observer_interval * 2:
                    # (v1.3 自进化闭合) 不直接否决——先尝试 Reflect→Generate 生成修复规则
                    # 这是 SOAR impasse→subgoal→chunk 模式：僵局是新能力诞生的机会
                    print(f"  [OBSERVER] 僵局持续——触发 Reflect → Generate → CodeGen...")
                    reflect_outcome = _reflect_and_extend_scanner(
                        skill_name, rounds, max_rounds, round_num
                    )
                    if reflect_outcome.get("extended"):
                        print(f"  [OBSERVER] Scanner 已扩展 → 继续进化")
                        converged = False
                        consecutive_zero = 0
                        continue
                    # Only escalate to human if Reflect couldn't generate rules
                    print(f"  [OBSERVER-VETO] Reflect 无法生成新规则 → 人工审查需要")
                    print(f"  [OBSERVER-VETO] 定时检测到持续问题 → 建议暂停并人工审查")
                    memory.write_event(skill_name, "loop", {
                        "phase": "periodic_observer_alert",
                        "stubborn": list(unknown_stubborn.keys()),
                        "trace_fails": persistent if trace_fails else {},
                        "round": round_num
                    })
                    # Only escalate to full veto after 2 observer cycles of issues
                    if not auto_fix_suspended:
                        auto_fix_suspended = True
                        suspension_reason = (
                            f"Periodic observer alert at round {round_num}: "
                            f"{'persistent trace failures' if trace_fails else ''}"
                            f"{'unknown stubborn patterns' if unknown_stubborn else ''}"
                        )
                    print(f"  [OBSERVER-VETO] 自动修复暂停——仅继续扫描监控")
            except ImportError as _ie:
                _warn_import(skill_name, "optional_module", str(_ie))

        if converged:
            print(f"\n  ── 收敛达成 ──")
            print(f"  {stop_rounds} 轮连续零发现")

            # ── (v1.2 自进化回路) 收敛不是终点，是新一轮探索的起点 ──
            # 运行 Reflect + 生成新 scanner 规则 + 重新扫描
            # 如果发现新问题 → 取消收敛，继续循环
            # 如果没有新问题 → 真正收敛
            reflect_outcome = _reflect_and_extend_scanner(
                skill_name, rounds, max_rounds, round_num
            )
            # (v1.3) Observer hard veto: critical unfixable issues → escalate to human
            if reflect_outcome.get("observer_veto"):
                print(f"\n  [OBSERVER-VETO] Observer 检测到需人工介入的问题:")
                for r in reflect_outcome.get("veto_reasons", [])[:5]:
                    print(f"    ⚠️  [{r.get('severity','?')}] {r['reason'][:150]}")
                print(f"  [OBSERVER-VETO] → 暂停自改进，等待人工审查")
                memory.write_event(skill_name, "loop", {
                    "phase": "observer_veto",
                    "veto_reasons": reflect_outcome.get("veto_reasons", []),
                    "round": round_num
                })
                auto_fix_suspended = True
                suspension_reason = "Observer veto: " + "; ".join(
                    r["reason"][:80] for r in reflect_outcome.get("veto_reasons", [])[:3]
                )
                # Don't break — continue scanning but without fixes
                converged = False
                continue

            if reflect_outcome.get("extended"):
                new_findings = reflect_outcome.get("new_findings", 0)
                print(f"  [EVOLVE] Scanner 扩展了搜索空间 → 发现 {new_findings} 个新问题")
                print(f"  → 继续进化...")
                converged = False
                consecutive_zero = 0  # Reset — new findings found
                continue  # Jump to next round
            else:
                print(f"  [EVOLVE] Reflect 未发现新的可检测模式 → 真正收敛")
                print(f"  ── 停止 ──")
                break

    # Log loop end
    memory.write_event(skill_name, "loop", {
        "phase": "end",
        "rounds": len(rounds),
        "converged": rounds[-1]["converged"] if rounds else False,
        "total_findings": sum(r["findings_count"] for r in rounds),
        "total_fixes": sum(r["fixes_applied"] for r in rounds)
    })

    # Write metrics
    memory.write_metric(skill_name, "convergence_rounds", len(rounds))
    memory.write_metric(skill_name, "total_findings",
                        sum(r["findings_count"] for r in rounds))
    # v1.2: false_convergence_rate 从二元跳变改为滑动平均
    # 单轮: 0=真收敛, 1=假收敛(有findings但声明converged)
    last_converged = rounds[-1]["converged"] if rounds else False
    last_findings = rounds[-1]["findings_count"] if rounds else 0
    false_conv = 0.0 if (last_converged and last_findings == 0) else (
        1.0 if (last_converged and last_findings > 0) else 0.5  # 未收敛=中性
    )
    # Blend with history to avoid dead-value flagging
    try:
        prev = memory.get_latest_metric(skill_name, "false_convergence_rate")
        if prev is not None and isinstance(prev, (int, float)) and prev > 0:
            false_conv = false_conv * 0.6 + prev * 0.4  # EWMA smoothing
    except (TypeError, ValueError):
        pass  # TODO: log or re-raise
    memory.write_metric(skill_name, "false_convergence_rate", false_conv)

    # (GVU) Write stability metrics — include diagnostic metadata
    gvu_end = memory.calculate_gvu_snr(skill_name)
    details = gvu_end.get("details", {})
    gvu_meta = {
        "verifier_source": details.get("verifier_source", "?"),
        "execution_traces": details.get("execution_traces", 0),
        "trace_successes": details.get("trace_successes", 0),
        "generator_noise": gvu_end["generator_noise"],
        "stability_ratio": gvu_end["stability_ratio"],
    }
    # Signal insufficient_data when SNR is unverified (no independent traces)
    snr_source = details.get("verifier_source", "?")
    if snr_source in ("unverified", "no_data", "execution_traces_insufficient", "no_data"):
        gvu_meta["insufficient_data"] = True
    memory.write_metric(skill_name, "generator_noise", gvu_end["generator_noise"], gvu_meta)
    # v1.2: verifier_snr may be None (legitimate "no data" state)
    memory.write_metric(skill_name, "verifier_snr",
                        gvu_end["verifier_snr"] if gvu_end["verifier_snr"] is not None else 1.0,
                        gvu_meta)

    # (v1.2) Save final checkpoint with actual convergence state
    true_converged = rounds[-1]["converged"] if rounds else False
    last_findings = rounds[-1]["findings_count"] if rounds else 0
    try:
        from engine import checkpoint as ckpt
        ckpt.save_checkpoint_final(skill_name, len(rounds), {
            "findings_count": last_findings,
            "consecutive_zero": consecutive_zero,
            "fix_fingerprints": fix_fingerprints,
            "proposals_active": len([r for r in rounds if r.get("impasse")]),
            "total_fixes_applied": sum(r.get("fixes_applied", 0) for r in rounds),
            "converged": true_converged
        })
    except ImportError as _ie:
        _warn_import(skill_name, optional_module, str(_ie))

    # (P9) Auto-trigger hillclimb if fitness stagnating
    try:
        from engine import hillclimb as hc
        if hc.should_hillclimb(skill_name):
            print(f"\n  [HILLCLIMB] 适应度停滞检测——自动爬山优化...")
            hc_result = hc.run_hillclimb(skill_name)
            print(f"  [HILLCLIMB] {hc_result.get('verdict', '?')}")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # (P6) Update metaproductivity aggregates
    try:
        memory.update_descendant_quality_aggregate(skill_name)
    except AttributeError:
        pass  # TODO: log or re-raise

    # (P10) Clear checkpoint on successful completion
    try:
        from engine import checkpoint as ckpt
        ckpt.clear_checkpoint(skill_name)
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # ── (A) Rule aging — every 10 rounds, clean up unused generated rules ──
    if len(rounds) >= 10:
        try:
            from engine.system2 import rule_generator as rg
            print(f"\n  [A:老化] 规则老化检查...")
            age_result = rg.age_rules(skill_name)
            if age_result.get("actions"):
                print(f"  [A:老化] {age_result['verdict']}")
                for a in age_result["actions"]:
                    print(f"    {a}")
        except ImportError as _ie:
            _warn_import(skill_name, "optional_module", str(_ie))

    # ── (C) Memory maintenance — merge duplicates, decay stale docs ──
    try:
        from engine import memory_index as mi
        mem_result = mi.auto_maintain_memory(skill_name)
        print(f"  [C:记忆] {mem_result['verdict']}")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # (S7: Red-Team) Adversarial audit of verification gates
    try:
        from engine import redteam
        overdue = redteam.check_since_last_audit(skill_name)
        if overdue.get("should_run") or len(rounds) >= 15:
            print("\n  [S7:RedTeam] verification system audit...")
            rt = redteam.run_red_team_audit(skill_name)
            print(f"  [S7:RedTeam] {rt['verdict']}")
            if rt.get("missed"):
                memory.write_insight(skill_name,
                    f"S7: {rt['missed']} defects bypassed gates", [], confidence=0.9)
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # (S8: Regression) Golden test suite — run if scanner/rules/patterns changed
    try:
        from engine import regression_guard
        guard = regression_guard.run_regression_suite(skill_name)
        if guard.get("should_block"):
            print(f"  [S8:Regression] {guard['verdict']}")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # (S9: Skill Composer) Auto-synthesize compound rules
    if len(rounds) >= 5:
        try:
            from engine import skill_composer
            synth = skill_composer.auto_synthesize_compounds(skill_name)
            if synth["compounds_proposed"] > 0:
                print(f"  [S9:Composer] {synth['verdict']}")
        except ImportError as _ie:
            _warn_import(skill_name, "optional_module", str(_ie))

    # ── (S1: Socratic-SWE) Trace distillation — every 5 rounds ──
    if len(rounds) >= 5:
        try:
            from engine import trace_distiller as td
            print(f"\n  [S1:轨迹蒸馏] 从 {len(rounds)} 轮求解轨迹中蒸馏技能...")
            distill_result = td.run_trace_distillation(skill_name, lookback=30, auto_rule=True)
            print(f"  [S1:轨迹蒸馏] {distill_result['verdict']}")
        except ImportError as _ie:
            _warn_import(skill_name, "optional_module", str(_ie))

    # ── (S3: Mem²Evolve) Co-evolution health check ──
    try:
        from engine import memory_index as mi
        evo = mi.coevolution_stats(skill_name)
        if evo["total_links"] > 0:
            print(f"  [S3:共进化] {evo['verdict']} (反馈比={evo['feedback_ratio']:.2f})")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    # ── (v1.2 CodeGen) 为缺少可执行代码的试用规则生成 fix 函数 ──
    try:
        from engine.system2 import code_generator as cg
        cg_result = cg.try_generate_for_pending_rules(skill_name)
        if cg_result.get("pending", 0) > 0:
            print(f"  [CodeGen] {cg_result.get('verdict', '?')}")
    except ImportError as _ie:
        _warn_import(skill_name, "optional_module", str(_ie))

    return {
        "skill": skill_name,
        "total_rounds": len(rounds),
        "converged": rounds[-1]["converged"] if rounds else False,
        "total_findings": sum(r["findings_count"] for r in rounds),
        "total_fixes": sum(r["fixes_applied"] for r in rounds),
        "gvu_stability": gvu_end,
        "rounds": rounds
    }


def _trigger_system2(skill_name: str, findings: list, impasse: dict):
    """Trigger Layer 2 deep analysis on impasse."""
    memory.write_event(skill_name, "reflect", {
        "trigger": "impasse",
        "reasons": impasse.get("reasons", []),
        "findings_count": len(findings),
        "patterns": list(set(f["pattern"] for f in findings))
    })


# ── CLI ────────────────────────────────────────────────────────────────────────

def _run_claude_step_scan(args):
    """Claude-step: 仅扫描——不做任何修复决策。"""
    import json as _json
    gen = generate_fixes(args.target)
    if args.json:
        print(_json.dumps(gen, indent=2, ensure_ascii=False))
    else:
        print(f"  [SCAN] {gen['findings_count']} findings")
        for f in gen["findings"]:
            icon = {"critical": "!!!", "warning": "[!]", "info": "[i]"}.get(
                f.get("severity", ""), "?")
            print(f"  {icon} [{f.get('pattern', '?')}] {f.get('file', '?')}:{f.get('line', '?')}")
            print(f"      {f.get('description', '')}")
            print(f"      匹配: {f.get('match', '')[:120]}")


def _run_claude_step_evaluate(args):
    """Claude-step: 评估 findings——哪些规则匹配、每个规则的质量如何。"""
    import json as _json
    gen = generate_fixes(args.target)
    ev = evaluate_fixes(args.target, gen["findings"])

    output = {
        "findings": gen,
        "evaluation": {
            "auto_fixable": ev["auto_fixable"],
            "needs_system2": ev["needs_system2"],
            "auto_rules": ev["auto_rules"],
            "rule_quality": {
                rname: {"success_rate": q.get("last_10_rate", 1.0),
                        "total_fixes": q.get("total", 0),
                        "verdict": ("LOW_QUALITY" if q.get("last_10_rate", 1.0) < 0.5
                                    and q.get("total", 0) >= 5 else "OK")}
                for rname, q in ev.get("rule_quality", {}).items()
            }
        }
    }

    if args.json:
        print(_json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"  [EVALUATE] auto_fixable={ev['auto_fixable']} needs_system2={ev['needs_system2']}")
        for r in ev["auto_rules"]:
            q = ev.get("rule_quality", {}).get(r["rule"], {})
            print(f"    [{r['rule']}] → {r['file']} "
                  f"(quality={q.get('last_10_rate', '?'):.0%} "
                  f"over {q.get('total', 0)} fixes)")


def _run_claude_step_apply(args):
    """Claude-step: 仅应用 Claude 筛选后的指定修复——不是全部 auto_apply。

    Claude 必须先运行 context → 判断哪些值得修 → 传 --fixes JSON 到这里。
    """
    import json as _json

    root = _find_skill_root(args.target)
    if not root:
        print(_json.dumps({"error": f"Skill '{args.target}' not found"}))
        sys.exit(1)

    if not args.fixes:
        print(_json.dumps({"error": "需要 --fixes JSON。Claude: 先运行 scan → 判断 → 传入选中的修复。",
                           "hint": '--fixes \'[{"rule":"add-utf8-header","file":"engine/loop.py","line":1}]\''}))
        sys.exit(1)

    try:
        selected_fixes = _json.loads(args.fixes)
    except _json.JSONDecodeError as e:
        print(_json.dumps({"error": f"--fixes JSON 解析失败: {e}"}))
        sys.exit(1)

    # Build matching findings from the Claude-selected fix list
    all_findings = generate_fixes(args.target)["findings"]
    matched = []
    # Normalize path separators for cross-platform matching
    def _norm_path(p):
        return str(p).replace('\\', '/')
    for sel in selected_fixes:
        rule_name = sel.get("rule", "")
        file_path = _norm_path(sel.get("file", ""))
        for f_item in all_findings:
            if _norm_path(f_item.get("file", "")) == file_path:
                # Check if any active rule matches
                from engine.system1 import rules as rules_mod
                active = rules_mod.get_active_rules(args.target)
                for rule in active:
                    if rule["name"] == rule_name and rule["pattern"] == f_item["pattern"]:
                        matched.append(f_item)
                        break

    if not matched:
        print(_json.dumps({"applied": 0, "skipped": len(selected_fixes),
                           "verdict": "[!!] 没有 finding 匹配指定的修复规则",
                           "hint": "运行 scan 确认文件/规则名正确"}))
        sys.exit(1)

    # Apply only the matched fixes
    from engine.system1 import rules as rules_mod
    fix_report = rules_mod.apply_fixes(args.target, matched)

    if args.json:
        print(_json.dumps(fix_report, indent=2, ensure_ascii=False))
    else:
        print(f"  [APPLY] applied={fix_report.get('applied', 0)} "
              f"skipped={fix_report.get('skipped', 0)}")
        for f in fix_report.get("fixes", []):
            icon = "[OK]" if f.get("applied") else "[--]"
            print(f"  {icon} {f.get('rule', '?')}: {f.get('finding_file', '?')} "
                  f"— {f.get('action', f.get('reason', '?'))}")


def _run_claude_step_verify(args):
    """Claude-step: 独立验证——展示修复的实际效果供 Claude 判断。

    输出修复的完整 diff 和验证命令结果，不自动判断 success。
    Claude 自己决定这是 GENUINE_FIX 还是 PSEUDO_FIX。

    支持两种模式:
      1. 从 event log 读取最近的 fix 事件
      2. 从 --fixes JSON 直接验证（apply 后立即 verify）
    """
    import json as _json

    root = _find_skill_root(args.target)
    if not root:
        print(_json.dumps({"error": f"Skill '{args.target}' not found"}))
        sys.exit(1)

    verification_items = []

    # Mode 1: --fixes JSON provided → verify specific fix results
    fix_list = []
    if hasattr(args, 'fixes') and args.fixes:
        try:
            fix_list = _json.loads(args.fixes)
        except _json.JSONDecodeError:
            fix_list = []
        for fix in fix_list:
            file_path = fix.get("file", fix.get("finding_file", ""))
            if not file_path:
                continue
            fp = root / file_path
            item = {
                "rule": fix.get("rule", "?"),
                "file": file_path,
                "action": fix.get("action", fix.get("reason", "?")),
            }
            if fp.exists():
                try:
                    content = fp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = ""
                item["has_todo_marker"] = "# TODO" in content
                item["file_lines"] = len(content.split("\n"))
                item["file_preview"] = content[:3000]
                if fp.suffix == ".py":
                    try:
                        compile(content, str(fp), "exec")
                        item["syntax_ok"] = True
                    except SyntaxError as e:
                        item["syntax_ok"] = False
                        item["syntax_error"] = str(e)
            verification_items.append(item)
    else:
        # Mode 2: Read from event log
        recent_fixes = memory.read_events(args.target, limit=10)
        fix_events = [e for e in recent_fixes if e["event_type"] == "fix"
                      and e.get("data", {}).get("phase") != "descendant_link"
                      and e.get("data", {}).get("phase") != "claude_verdict"]

        if not fix_events:
            print(_json.dumps({"verdict": "[--] 无可验证的修复",
                               "instruction": "先运行 apply 再运行 verify，或用 --fixes 直接指定"}))
            return

        latest_fix = fix_events[0]
        fix_data = latest_fix.get("data", {})
        fixes_detail = fix_data.get("fixes", {})

        for detail in fixes_detail.get("details", []):
            file_path = detail.get("file", detail.get("finding_file", ""))
            if not file_path:
                continue
            fp = root / file_path
            item = {
                "rule": detail.get("rule", "?"),
            "file": file_path,
            "action": detail.get("action", detail.get("reason", "?")),
        }

        # Show the file content around the changed area
        if fp.exists():
            content = fp.read_text(encoding="utf-8")
            # Find TODO markers (most fixes just add comments)
            item["has_todo_marker"] = "# TODO" in content or "# TODO:" in content
            item["file_lines"] = len(content.split("\n"))
            item["file_preview"] = content[:2000]

        # Run syntax check
        if fp.suffix == ".py" and fp.exists():
            try:
                compile(content, str(fp), "exec")
                item["syntax_ok"] = True
            except SyntaxError as e:
                item["syntax_ok"] = False
                item["syntax_error"] = str(e)

        verification_items.append(item)

    result = {
        "fixes_applied": len(verification_items),
        "verification_items": verification_items,
        "instruction": (
            "Claude: 阅读每个 verification_item。"
            "如果 action 只是 '标注'/'annotated'/'added TODO' → 这是 PSEUDO_FIX。"
            "如果 action 改变了代码行为 → 可能是 GENUINE_FIX。"
            "判断后用 verdict.py record 写入你的判断。"
        ),
        "claude_prompt": (
            "对每个修复，调用:\n"
            "python engine/verdict.py <skill> record "
            "--rule <rule_name> --file <file> "
            "--verdict <GENUINE_FIX|PSEUDO_FIX|...> "
            "--explanation '你的解释' "
            "--evidence '你看到的证据'"
        )
    }

    if args.json:
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"  [VERIFY] {len(verification_items)} fixes to verify")
        for item in verification_items:
            has_todo = item.get("has_todo_marker", False)
            icon = "⚠️ PSEUDO?" if has_todo else "  ✓"
            print(f"  {icon} {item['rule']}: {item['file']}")
            print(f"      action: {item.get('action', '?')}")


def _run_claude_step_context(args):
    """Claude-step: 输出完整决策上下文（委托给 context.py）。"""
    import json as _json
    from engine import context as ctx_mod
    ctx = ctx_mod.build_context(args.target, compact=args.compact)
    if args.json:
        print(_json.dumps(ctx, indent=2, ensure_ascii=False))
    else:
        # Human-readable summary
        f = ctx.get("findings", {})
        s = ctx.get("stability", {})
        print(f"  [CONTEXT] {args.target}")
        print(f"  Findings: {f.get('total', 0)} "
              f"(critical={f.get('by_severity', {}).get('critical', 0)} "
              f"warning={f.get('by_severity', {}).get('warning', 0)} "
              f"info={f.get('by_severity', {}).get('info', 0)})")
        print(f"  Active Rules: {len(ctx.get('rules', []))}")
        print(f"  Fix Qualities: {len(ctx.get('fix_qualities', {}))} rules tracked")
        print(f"  Recent Events: {len(ctx.get('recent_events', []))}")
        print(f"  Execution Traces: {len(ctx.get('execution_traces', []))}")
        print(f"  Insights: {len(ctx.get('insights', []))}")
        gvu = s.get("gvu", {})
        print(f"  GVU: {gvu.get('verdict', '?')}")
        print(f"  Fitness: {s.get('fitness', {}).get('unified_fitness', '?')}")
        print(f"  Previous Claude Verdicts: {len(ctx.get('previous_claude_verdicts', []))}")

        # Show findings with rule info
        print(f"\n  ── Findings Detail ──")
        for f_item in f.get("items", [])[:20]:
            pattern = f_item.get("pattern", "?")
            # Find matching rule
            matching = [r for r in ctx.get("rules", [])
                       if r.get("pattern") == pattern]
            rule_info = ""
            if matching:
                r = matching[0]
                rule_info = f" → rule={r['name']} auto={r.get('auto_apply', False)}"
                if r.get("has_fix_function"):
                    rule_info += " has_fix=True"
            print(f"  [{f_item.get('severity', '?')}] {pattern}: "
                  f"{f_item.get('file', '?')}:{f_item.get('line', '?')}{rule_info}")
            print(f"      {f_item.get('description', '')}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="v4 Claude-driven evolution: 逐步工具模式 + 传统自动循环"
    )
    sub = parser.add_subparsers(dest="cmd")

    # ── v4 Claude-driven step commands ──
    p = sub.add_parser("scan", help="[Claude-step 1] 仅扫描 findings——不做修复")
    p.add_argument("--target", required=True, help="目标 skill")
    p.add_argument("--json", action="store_true", help="JSON 输出")

    p = sub.add_parser("evaluate", help="[Claude-step 2] 评估 findings 和规则匹配")
    p.add_argument("--target", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("apply", help="[Claude-step 3] 仅应用 Claude 筛选后的修复")
    p.add_argument("--target", required=True)
    p.add_argument("--fixes", required=True, help='JSON: [{"rule":"...","file":"...","line":...}]')
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("verify", help="[Claude-step 4] 展示修复效果供 Claude 独立判断")
    p.add_argument("--target", required=True)
    p.add_argument("--fixes", default=None, help="可选: 直接指定要验证的修复 JSON (apply 的输出)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("context", help="[Claude-step 0] 输出完整决策上下文")
    p.add_argument("--target", required=True)
    p.add_argument("--compact", action="store_true")
    p.add_argument("--json", action="store_true")

    # ── Legacy auto-loop (for backward compatibility) ──
    p = sub.add_parser("auto", help="[Legacy] 传统自动进化循环（Python 驱动）")
    p.add_argument("--target", required=True, help="目标 skill 名称")
    p.add_argument("--max-rounds", type=int, help="最大迭代轮数")
    p.add_argument("--stop-rounds", type=int, help="收敛阈值")
    p.add_argument("--layer1-only", action="store_true",
                   help="仅 Layer 1（不触发 System 2）")
    p.add_argument("--dry-run", action="store_true",
                   help="仅生成和评估，不执行修复")

    # ── Utility commands ──
    p = sub.add_parser("gvu", help="GVU 稳定性检查")
    p.add_argument("--target", required=True)

    p = sub.add_parser("archive", help="DGM 存档")
    p.add_argument("--target", required=True)

    args = parser.parse_args()

    # ── v4 Claude-step dispatch ──
    if args.cmd == "context":
        _run_claude_step_context(args)
    elif args.cmd == "scan":
        _run_claude_step_scan(args)
    elif args.cmd == "evaluate":
        _run_claude_step_evaluate(args)
    elif args.cmd == "apply":
        _run_claude_step_apply(args)
    elif args.cmd == "verify":
        _run_claude_step_verify(args)

    # ── Legacy auto-loop ──
    elif args.cmd == "auto":
        if hasattr(args, 'dry_run') and args.dry_run:
            gen = generate_fixes(args.target)
            print(f"  [DRY-RUN] {gen['findings_count']} findings")
            ev = evaluate_fixes(args.target, gen["findings"])
            print(f"  [DRY-RUN] auto={ev['auto_fixable']} system2={ev['needs_system2']}")
            for f_item in gen["findings"][:20]:
                print(f"    [{f_item.get('severity', '?')}] {f_item.get('pattern', '?')}: "
                      f"{f_item.get('file', '?')}:{f_item.get('line', '?')} — "
                      f"{f_item.get('description', '')[:100]}")
            gvu = memory.calculate_gvu_snr(args.target)
            print(f"\n  [GVU] {gvu['verdict']}")
            return

        report = run_loop(
            args.target,
            max_rounds=args.max_rounds,
            stop_rounds=args.stop_rounds,
            layer1_only=args.layer1_only
        )
        if "error" in report:
            print(f"[!!] {report['error']}")
            sys.exit(1)
        print(f"\n{'='*50}")
        print(f"  进化循环报告")
        print(f"{'='*50}")
        print(f"  技能: {report['skill']}")
        print(f"  轮数: {report['total_rounds']}")
        print(f"  收敛: {'[OK]' if report['converged'] else '[--]'}")
        print(f"  发现: {report['total_findings']} | 修复: {report['total_fixes']}")
        gvu = report.get("gvu_stability", {})
        if gvu:
            print(f"  GVU:  {gvu.get('verdict', '?')}")
        print(f"{'='*50}")

    # ── Utility dispatch ──
    elif args.cmd == "gvu":
        gvu = memory.calculate_gvu_snr(args.target)
        print(f"  GVU: {gvu['verdict']}")
        print(f"  生成器噪声: {gvu['generator_noise']}")
        print(f"  验证器 SNR: {gvu['verifier_snr']}")
        print(f"  稳定性比率: {gvu['stability_ratio']}")

    elif args.cmd == "archive":
        aid = archive_if_diverse(args.target, 10)
        if aid:
            print(f"[OK] DGM archive: {aid}")
        else:
            print("[--] No archive needed")

    else:
        parser.print_help()
        print(f"\n  v4 Claude-driven workflow:")
        print(f"    python engine/loop.py context --target <skill> --json")
        print(f"      → Claude 读取完整上下文")
        print(f"    python engine/loop.py scan --target <skill>")
        print(f"      → Claude 判断哪些值得修")
        print(f"    python engine/loop.py apply --target <skill> --fixes '<json>'")
        print(f"      → Claude 调用指定修复")
        print(f"    python engine/loop.py verify --target <skill>")
        print(f"      → Claude 独立验证 + 写判断")
        print(f"    python engine/verdict.py <skill> record ...")
        print(f"      → Claude 记录真实判断")


if __name__ == "__main__":
    main()
