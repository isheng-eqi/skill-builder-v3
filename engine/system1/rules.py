#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 1: Rules engine — deterministic fixes for known anti-patterns.

Each rule: (name, condition_fn, fix_fn, description)
- condition_fn(root, finding) -> bool  — should this fix be applied?
- fix_fn(root, finding) -> dict        — apply the fix, return result

v1.1 additions (2026-06-13):
  - TAPO fix quality tracking: auto_apply confidence auto-adjusted by fix success rate
  - Rule demotion: rules with < 50% recent success rate lose auto_apply
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Individual fix functions ──────────────────────────────────────────────────

def fix_add_utf8_header(root: Path, finding: dict) -> dict:
    """Add UTF-8 stdout header to a Python script that lacks it."""
    fp = root / finding["file"]
    if not fp.exists():
        return {"applied": False, "error": f"File not found: {finding['file']}"}

    content = fp.read_text(encoding="utf-8")
    if "sys.stdout.reconfigure" in content:
        return {"applied": False, "reason": "already has UTF-8 header"}

    # Insert after shebang line
    lines = content.split("\n")
    new_lines = [lines[0]]
    if lines[0].startswith("#!/"):
        new_lines.append("import sys")
        new_lines.append("sys.stdout.reconfigure(encoding='utf-8')")
        new_lines.extend(lines[1:])
    else:
        new_lines = ["#!/usr/bin/env python3", "import sys",
                     "sys.stdout.reconfigure(encoding='utf-8')"] + lines

    new_content = "\n".join(new_lines)
    fp.write_text(new_content, encoding="utf-8")
    return {"applied": True, "file": str(finding["file"]), "action": "added UTF-8 header"}


def fix_hardcoded_versions(root: Path, finding: dict) -> dict:
    """Replace hardcoded version constraint with dynamic lookup (stub).

    This is a best-effort fix. Full fix requires understanding the context.
    """
    fp = root / finding["file"]
    if not fp.exists():
        return {"applied": False, "error": f"File not found: {finding['file']}"}

    content = fp.read_text(encoding="utf-8")
    match_text = finding.get("match", "")
    if not match_text or ">=" not in match_text:
        return {"applied": False, "reason": "cannot parse version constraint"}

    # Add comment suggesting dynamic lookup
    line_num = finding.get("line", 0)
    lines = content.split("\n")
    if 0 < line_num <= len(lines):
        original = lines[line_num - 1]
        if "hardcoded" not in original.lower():
            lines[line_num - 1] = original + "  # TODO: 从 manifest.json 动态读取版本约束"
    else:
        return {"applied": False, "reason": "line number out of range"}

    fp.write_text("\n".join(lines), encoding="utf-8")
    return {"applied": True, "file": str(finding["file"]),
            "action": f"标注硬编码版本: {match_text}"}


def fix_grep_pcre(root: Path, finding: dict) -> dict:
    """Replace grep -P with portable equivalent (grep -E)."""
    fp = root / finding["file"]
    if not fp.exists():
        return {"applied": False, "error": f"File not found: {finding['file']}"}

    content = fp.read_text(encoding="utf-8")
    match_text = finding.get("match", "")

    # Simple replacement: grep -nP -> grep -nE
    if "grep -nP" in content:
        content = content.replace("grep -nP", "grep -nE")
        content = content.replace("grep -P", "grep -E")
        fp.write_text(content, encoding="utf-8")
        return {"applied": True, "file": str(finding["file"]),
                "action": "grep -P → grep -E (portable)"}

    return {"applied": False, "reason": "no grep -P found in file"}


# ── (skill-auditor) new fix functions ──────────────────────────────────────

def fix_bare_except(root: Path, finding: dict) -> dict:
    """Replace bare except: with except Exception: for proper exception hygiene.

    bare except: catches KeyboardInterrupt and SystemExit — should never be caught blindly.
    except Exception: is the standard Python fix: lets system signals propagate while
    still catching all regular exceptions.

    This is a genuine behavioral change (v4.2 rewrite from pseudo-fix).
    """
    fp = root / finding["file"]
    if not fp.exists():
        return {"applied": False, "error": f"File not found: {finding['file']}"}
    content = fp.read_text(encoding="utf-8")
    lines = content.split("\n")
    line_num = finding.get("line", 0)
    if not (0 < line_num <= len(lines)):
        return {"applied": False, "reason": "line number out of range"}

    original = lines[line_num - 1]
    stripped = original.strip()

    # Skip: string literals, docstrings, comments — not real except
    if stripped.startswith(('"""', "'''", '"', "'")) or stripped.startswith('#'):
        return {"applied": False, "reason": "inside string/comment — not real except"}

    # Skip: already has Exception or already annotated
    if "Exception" in original or "BaseException" in original:
        return {"applied": False, "reason": "already has concrete exception type"}

    # Match bare "except:" or "except :" (optional space)
    if re.search(r'^(\s*)except\s*:', original):
        indent = original[:len(original) - len(original.lstrip())]
        # Replace except: → except Exception: preserving indentation
        new_line = re.sub(r'except\s*:', 'except Exception:', original)
        # Clean up old pseudo-fix TODO if present
        new_line = re.sub(r'\s*#\s*TODO:\s*specify concrete exception type\s*', '', new_line)
        lines[line_num - 1] = new_line

        # Verify the change doesn't break compilation
        if not _verify_compile(lines, fp):
            return {"applied": False, "reason": f"bare-except fix would break {fp.name} at line {line_num}"}

        fp.write_text("\n".join(lines), encoding="utf-8")
        return {"applied": True, "file": str(finding["file"]),
                "action": f"replaced bare except: → except Exception: at line {line_num}"}

    return {"applied": False, "reason": "no bare except: pattern found on this line"}


def _verify_compile(lines: list, fp: Path) -> bool:
    """Verify that modified lines still compile. Returns True if OK, False if SyntaxError.

    Called once per fix — not 11 times. (v4 fix: deduplicated 22 identical compile() blocks.)
    """
    try:
        compile("\n".join(lines), str(fp), 'exec')
        return True
    except SyntaxError:
        return False


def fix_except_pass(root: Path, finding: dict) -> dict:
    """Detect-only. Automated fix cannot determine the correct exception handling strategy.

    v4.2 honest declaration (per Constitution Article 4: 诚实即修复):
    Replacing 'pass' with 'pass  # TODO: log or re-raise' is a pseudo-fix.
    It adds a comment without changing behavior. The right action depends on context:
      - ImportError for optional dependency → pass is correct
      - Runtime error in critical path → should log or re-raise
      - Expected edge case with fallback → need explicit handling

    This rule flags findings. Claude (or human) decides the right action.
    """
    return {"applied": False,
            "reason": "detect-only: 自动修复无法判断应日志/重抛/忽略——需人工上下文判断"}


# ── Rule registry ─────────────────────────────────────────────────────────────

# v1.3: SEED_RULES 是不可变的初始种子规则。
# 活跃规则集 = SEED_RULES ∪ promoted_generated_rules。
# 生成规则通过 probation → 晋升后写入 data/active_rules.json → 永续加入活跃集。
# 这是自指闭环的物理实现：系统能进化自己的检测/修复能力。

SEED_RULES = [
    {
        "name": "add-utf8-header",
        "pattern": "missing-utf8-header",
        "severity": "critical",
        "auto_apply": True,  # System 1 can apply without System 2
        "fix": fix_add_utf8_header,
        "description": "为缺少 UTF-8 头的 Python 脚本添加 stdout 重配置"
    },
    {
        "name": "fix-grep-pcre",
        "pattern": "grep-pcre",
        "severity": "critical",
        "auto_apply": False,  # v1.2: 唯一的候选文件 constitution.md 在 scanner exclude 中，15轮零应用
        "fix": fix_grep_pcre,
        "description": "替换 grep -P 为便携的 grep -E——需要人工审查"
    },
    {
        "name": "tag-hardcoded-versions",
        "pattern": "hardcoded-versions",
        "severity": "warning",
        "auto_apply": False,  # v4: PSEUDO_FIX — 只加 TODO 注释，不改变代码行为。需重写 fix 函数。
        "fix": fix_hardcoded_versions,
        "description": "标注硬编码版本约束——detect-only 直到 fix 函数被改进为真正的代码修改"
    },
    # Patterns below are "detect only" — System 1 flags them but cannot auto-fix
    {
        "name": "language-drift-detect",
        "pattern": "language-drift",
        "severity": "warning",
        "auto_apply": False,  # Requires human/LLM judgment
        "description": "英文哨兵检测——需要人工判断是否真的需要中文化"
    },
    {
        "name": "self-reference-detect",
        "pattern": "self-reference",
        "severity": "critical",
        "auto_apply": False,  # Never auto-fix — requires architectural review
        "description": "自指特殊路径检测——需要架构审查"
    },
    # ── (来自 skill-auditor 蒸馏) ──
    # v4.2: fix_bare_except 已重写为真正的代码修改 (except: → except Exception:)
    # fix_except_pass 是诚实 detect-only —— 自动修复无法确定正确的异常处理策略
    {
        "name": "fix-bare-except",
        "pattern": "bare-except",
        "severity": "warning",
        "auto_apply": True,  # v4.2: GENUINE_FIX — 将 except: 替换为 except Exception:，真正改变控制流
        "fix": fix_bare_except,
        "description": "替换裸 except: 为 except Exception:——让 KeyboardInterrupt/SystemExit 能正常传播"
    },
    {
        "name": "tag-except-pass",
        "pattern": "except-pass",
        "severity": "warning",
        "auto_apply": False,  # v4.2: 诚实 detect-only。自动修复无法判断应日志/重抛/忽略——需人工上下文。
        "fix": fix_except_pass,
        "description": "检测 except...pass 静默吞异常——需人工判断正确的处理策略（日志？重抛？忽略？）"
    },
]


# ── (v1.3) Dynamic active rule set ─────────────────────────────────────────────

def _active_rules_path(skill_name: str) -> Optional[Path]:
    """Get the path to data/active_rules.json for a skill."""
    root = _find_skill_root(skill_name)
    if not root:
        return None
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "active_rules.json"


def load_persisted_active_rules(skill_name: str) -> list:
    """Load persisted promoted rules from data/active_rules.json.

    These are generated rules that passed probation and were promoted.
    They persist across sessions and are merged with SEED_RULES each time.

    v4.2: on-load dedup repair — removes entries with duplicate name+pattern.
    Writes back the cleaned version so duplicates don't accumulate.
    """
    path = _active_rules_path(skill_name)
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    promoted = data.get("promoted_rules", [])
    # v4.2 repair: dedup by (name, pattern), keep first occurrence
    seen = {}
    deduped = []
    removed = 0
    for r in promoted:
        key = (r.get("name", ""), r.get("pattern", ""))
        if key not in seen:
            seen[key] = True
            deduped.append(r)
        else:
            removed += 1
    if removed:
        data["promoted_rules"] = deduped
        data["_v4_2_dedup_repair"] = {
            "duplicates_removed": removed,
            "repaired_iso": datetime.now(timezone.utc).isoformat(),
            "note": "v3 auto-promotion created duplicates with different _rule_ids"
        }
        data["total_promoted"] = len(deduped)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return deduped


def register_promoted_rule(skill_name: str, rule: dict) -> bool:
    """Register a promoted rule to the persistent active rule set.

    Called by rule_generator when a probation rule is promoted to active.
    The rule is converted to the RULES-compatible format and saved to
    data/active_rules.json so it survives across sessions.

    Returns True on success.
    """
    path = _active_rules_path(skill_name)
    if not path:
        return False

    # Load existing
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}

    promoted = data.get("promoted_rules", [])

    # Convert to active rule format
    active_entry = {
        "name": rule["name"],
        "pattern": rule["pattern"],
        "severity": rule.get("severity", "warning"),
        "auto_apply": True,  # Promoted = auto-apply
        "description": rule.get("description", ""),
        "_source": "promoted",
        "_rule_id": rule.get("rule_id", "?"),
        "_promoted_iso": datetime.now(timezone.utc).isoformat(),
        "_fix_strategy": rule.get("fix_strategy", ""),
        "_generated_code_path": rule.get("_generated_code_path", ""),
    }

    # Check for duplicate — two levels:
    # Level 1: same rule_id (exact same generation run)
    # Level 2: same name + pattern (different generation runs, identical fix)
    # v4.2 fix: v3 auto-promotion generated different _rule_ids for the same
    # underlying fix, causing 4 identical fix-metric-dead-value entries.
    existing_ids = {r.get("_rule_id") for r in promoted}
    if active_entry["_rule_id"] in existing_ids:
        # Update existing entry by rule_id
        for i, r in enumerate(promoted):
            if r.get("_rule_id") == active_entry["_rule_id"]:
                promoted[i] = active_entry
                break
    elif any(r["name"] == active_entry["name"] and r["pattern"] == active_entry["pattern"]
             for r in promoted):
        # Duplicate by name+pattern but different _rule_id — skip (v4.2 dedup)
        # Update the existing entry's metadata to track this was seen again
        for i, r in enumerate(promoted):
            if r["name"] == active_entry["name"] and r["pattern"] == active_entry["pattern"]:
                promoted[i]["_v4_dup_seen"] = True
                promoted[i]["_v4_last_seen_iso"] = datetime.now(timezone.utc).isoformat()
                break
    else:
        promoted.append(active_entry)

    # Also ensure auto_apply=True in the generated rule file
    try:
        from engine.system2 import rule_generator
        gen_rule = rule_generator.load_rule(skill_name, rule.get("rule_id", ""))
        if gen_rule:
            gen_rule["auto_apply"] = True
            gen_rule["status"] = "active"
            rule_generator.save_rule(skill_name, gen_rule)
    except ImportError:
        pass

    data["promoted_rules"] = promoted
    data["last_updated_iso"] = datetime.now(timezone.utc).isoformat()
    data["total_promoted"] = len(promoted)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def get_active_rules(skill_name: str = "") -> list:
    """Get the full active rule set: SEED_RULES ∪ persisted promoted rules.

    This is the SINGLE SOURCE OF TRUTH for what rules are active.
    All code paths that need the rule list MUST use this function.

    v1.3: This is where self-evolution becomes real.
    Promoted generated rules are merged with seed rules on every call,
    so they persist across sessions and are treated identically to seed rules.
    """
    active = list(SEED_RULES)  # Copy seed rules

    if skill_name:
        # Load persisted promoted rules
        persisted = load_persisted_active_rules(skill_name)
        for pr in persisted:
            # Skip if already in active list (by name)
            if any(r["name"] == pr["name"] for r in active):
                continue
            # Try to load generated fix function
            fix_fn = None
            code_path = pr.get("_generated_code_path", "")
            if code_path:
                try:
                    from engine.system2.code_generator import load_generated_fix
                    fix_fn = load_generated_fix(pr)
                except ImportError:
                    pass
            entry = {
                "name": pr["name"],
                "pattern": pr["pattern"],
                "severity": pr.get("severity", "warning"),
                "auto_apply": pr.get("auto_apply", True),
                "description": pr.get("description", ""),
                "_source": "promoted",
                "_rule_id": pr.get("_rule_id", "?"),
            }
            if fix_fn:
                entry["fix"] = fix_fn
            active.append(entry)

        # Also merge probation rules from rule_generator (for experimental use)
        try:
            from engine.system2 import rule_generator
            merged = rule_generator.merge_generated_rules(skill_name, [])
            # Only add probation rules not already in active
            for m in merged:
                if not any(r["name"] == m["name"] for r in active):
                    active.append(m)
        except ImportError:
            pass

    return active


# ── (v1.3) Backward-compatible alias for code that iterates seed rules ─────────
# Use get_active_rules(skill_name) for the full merged list.
# Use SEED_RULES directly only for iterating the immutable seed set (TAPO demotion etc.)


def get_applicable_rules(findings: list, skill_name: str = "") -> list:
    """Given a list of findings, return applicable rules.

    v1.1 (TAPO): Rules with degraded fix quality may have auto_apply disabled.
    v1.2 (Gödel Agent): Merges generated rules from rule_generator.py.
    v1.3: Uses get_active_rules() which includes SEED_RULES + promoted + probation.
    """
    # Get the full active rule set (seed + promoted + probation)
    merged_rules = get_active_rules(skill_name)

    applicable = []
    probation_trials = []  # v1.2: probation rules with executable fix functions
    for rule in merged_rules:
        # v1.2: Skip generated rules that don't have executable fix code yet
        if rule.get("_needs_code") and not rule.get("fix"):
            continue
        for f in findings:
            if f["pattern"] == rule["pattern"]:
                if rule.get("auto_apply", False):
                    applicable.append((rule, f))
                elif rule.get("_source") == "generated" and "fix" in rule:
                    # v1.2: Probation rule with fix function — try one trial
                    # Only take the first matching finding per rule per round
                    probation_trials.append((rule, f))
                    break  # One trial per rule per round
    # Append probation trials after auto-apply (System 2 experiments)
    applicable.extend(probation_trials)
    return applicable


def check_and_maybe_demote_rules(skill_name: str) -> dict:
    """(TAPO) Check fix quality for all rules and demote low-quality ones.

    A rule is demoted (auto_apply set to False) if:
    - It has 5+ recorded fixes
    - Its recent success rate (last 10) is below 50%

    Demoted rules can still be applied manually via --all flag.

    Returns dict of {rule_name: action_taken}.
    """
    from engine import memory

    changes = {}
    for rule in SEED_RULES:
        if not rule.get("auto_apply", False):
            continue

        quality = memory.get_fix_quality(skill_name, rule["name"])
        if quality["total"] < 5:
            continue

        if quality.get("last_10_rate", 1.0) < 0.5 and quality["total"] >= 5:
            old = rule.get("auto_apply")
            rule["auto_apply"] = False
            rule["_demotion_reason"] = (
                f"TAPO 自动降级: 最近 {quality['total']} 次修复成功率仅 "
                f"{quality['last_10_rate']:.0%} (阈值 50%)"
            )
            changes[rule["name"]] = {
                "was_auto_apply": old,
                "now_auto_apply": False,
                "success_rate": quality["last_10_rate"],
                "total_fixes": quality["total"]
            }

    return changes


def maybe_promote_rules(skill_name: str) -> dict:
    """(TAPO) Check if any demoted rules can be re-promoted.

    A rule can be re-promoted if:
    - It was previously demoted
    - Its recent success rate (last 10) is back above 70%
    """
    from engine import memory

    changes = {}
    for rule in SEED_RULES:
        if rule.get("auto_apply", False):
            continue  # Already active
        if not rule.get("_demotion_reason"):
            continue  # Wasn't demoted by TAPO

        quality = memory.get_fix_quality(skill_name, rule["name"])
        if quality.get("last_10_rate", 0.0) > 0.7 and quality["total"] >= 10:
            rule["auto_apply"] = True
            rule.pop("_demotion_reason", None)
            changes[rule["name"]] = {
                "was_auto_apply": False,
                "now_auto_apply": True,
                "success_rate": quality["last_10_rate"],
                "total_fixes": quality["total"]
            }

    return changes


def apply_fixes(skill_name: str, findings: list, auto_only: bool = True) -> dict:
    """Apply auto-fix rules to findings. Returns fix report."""
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在", "fixes": []}

    active = get_active_rules(skill_name)
    applicable = get_applicable_rules(findings, skill_name) if auto_only else [
        (rule, f) for rule in active for f in findings if f["pattern"] == rule["pattern"]
    ]

    fixes = []
    for rule, finding in applicable:
        try:
            result = rule["fix"](root, finding)
            result["rule"] = rule["name"]
            result["finding_file"] = finding["file"]
            fixes.append(result)
        except Exception as e:
            fixes.append({
                "applied": False,
                "rule": rule["name"],
                "finding_file": finding["file"],
                "error": str(e)
            })

    applied = [f for f in fixes if f.get("applied")]
    skipped = [f for f in fixes if not f.get("applied")]

    return {
        "skill": skill_name,
        "total_fixes": len(fixes),
        "applied": len(applied),
        "skipped": len(skipped),
        "fixes": fixes,
        "files_changed": list(set(f["file"] for f in applied if "file" in f))
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="System 1 规则引擎——确定性修复")
    parser.add_argument("skill",
                        help="目标 skill 名称")
    parser.add_argument("--findings-file", help="从扫描结果 JSON 文件读取 findings")
    parser.add_argument("--dry-run", action="store_true", help="仅显示会执行的修复")
    parser.add_argument("--all", action="store_true",
                        help="应用所有规则（包括非自动的）")

    args = parser.parse_args()

    # If findings file provided, load from it
    findings = []
    if args.findings_file:
        import json
        fp = Path(args.findings_file)
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8"))
            findings = data.get("findings", [])

    if not findings and not args.findings_file:
        # Run scanner first
        from engine.system1 import scanner
        scan_result = scanner.scan_skill(args.skill)
        findings = scan_result["findings"]

    if args.dry_run:
        active = get_active_rules(args.skill)
        applicable = get_applicable_rules(findings) if not args.all else [
            (rule, f) for rule in active for f in findings if f["pattern"] == rule["pattern"]
        ]
        print(f"会执行 {len(applicable)} 个修复:")
        for rule, f in applicable:
            print(f"  → {rule['name']}: {f['file']}:{f.get('line', '?')} — {rule['description']}")
        return

    report = apply_fixes(args.skill, findings, auto_only=not args.all)
    if "error" in report:
        print(f"[!!] {report['error']}")
        sys.exit(1)

    print(f"  修复报告: {args.skill}")
    print(f"  执行: {report['applied']} | 跳过: {report['skipped']} | 总计: {report['total_fixes']}")
    for f in report["fixes"]:
        icon = "[OK]" if f.get("applied") else "[--]"
        reason = f.get("action", f.get("reason", f.get("error", "?")))
        print(f"  {icon} {f.get('rule', '?')}: {f.get('finding_file', '?')} — {reason}")


if __name__ == "__main__":
    main()
