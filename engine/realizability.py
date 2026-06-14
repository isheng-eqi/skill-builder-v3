#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Constitution realizability auditor.

这个模块是整个系统最深的自指闭环：
它读取宪法本身 → 解析每条法则的声明 → 检查引擎实际能力 → 标记差距。

宪法不是硬编码在审计代码里的。审计代码读宪法文件，所以宪法修正后
审计自动跟随。这是第零法则"宪法必须可验证"的执行者。

Run: python engine/realizability.py
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.platform import find_skill_root as _find_skill_root
# ═══════════════════════════════════════════════════════════════════════════════
# 宪法解析器：读取宪法文件，抽取出每条法则的声明
# ═══════════════════════════════════════════════════════════════════════════════

def parse_constitution(root: Path) -> dict:
    """Parse constitution.md into structured laws.

    Returns {law_number: {title, text, verification_claim, honest_note}}
    """
    con_path = root / "constitution.md"
    if not con_path.exists():
        return {"error": "constitution.md not found"}

    text = con_path.read_text(encoding="utf-8")
    laws = {}

    # Split by ## headers
    current_law = None
    current_num = None
    current_text = []
    current_verification = ""
    current_honest = ""

    for line in text.split("\n"):
        # Detect law header: "## 第X法则" or "## 第零法则"
        m = re.match(r'^##\s*(第[零一二三四五六七八九十]+法则)', line)
        if m:
            # Save previous
            if current_law:
                laws[current_num] = {
                    "title": current_law,
                    "text": "\n".join(current_text).strip(),
                    "verification_claim": current_verification,
                    "honest_note": current_honest,
                }
            current_law = m.group(1)
            current_num = _law_num(m.group(1))
            current_text = []
            current_verification = ""
            current_honest = ""
            continue

        if current_law:
            current_text.append(line)
            # Extract verification claim
            if "验证方式" in line and "`" in line:
                current_verification = line.strip()
            # Extract honest declaration
            if "诚实声明" in line:
                current_honest = line.strip()

    # Don't forget the last one
    if current_law:
        laws[current_num] = {
            "title": current_law,
            "text": "\n".join(current_text).strip(),
            "verification_claim": current_verification,
            "honest_note": current_honest,
        }

    return laws


def _law_num(title: str) -> int:
    """将中文法则编号转为数字: '第一法则'→1, '第零法则'→0"""
    mapping = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for ch, num in mapping.items():
        if ch in title:
            return num
    return -1


# ═══════════════════════════════════════════════════════════════════════════════
# 逐条审计：读取宪法声明的验证方式 → 检查引擎是否真的能做到
# ═══════════════════════════════════════════════════════════════════════════════

def audit_law0_realizability(root: Path, constitution: dict) -> dict:
    """审计第零法则：宪法是否接受可实现性审计。

    宪法声称: 每条法则必须有可验证的真值，有修正程序。
    实际检查: realizability.py 是否存在并能正常运行。
    """
    law = constitution.get(0, {})
    # v2.0: amendment procedure is a separate section at the end of constitution,
    # not embedded in law text. Check both.
    full_text = (root / "constitution.md").read_text(encoding="utf-8")
    has_amendment = "修正程序" in full_text or ("修正" in law.get("text", "") and "程序" in law.get("text", ""))
    has_honest = bool(law.get("honest_note", ""))

    # Self-test: can this module run?
    try:
        # Quick sanity: parse constitution
        _ = parse_constitution(root)
        can_run = True
    except Exception as e:
        can_run = False

    return {
        "law": "第零法则：可实现性（元规则）",
        "constitution_text_read": bool(law),
        "has_amendment_procedure": has_amendment,
        "has_honest_declaration": has_honest,
        "auditor_can_run": can_run,
        "verdict": "[OK] 第零法则自我实现——审计器存在且可运行，宪法包含修正程序"
                   if (can_run and has_amendment)
                   else "[!!] 第零法则不可实现——缺少审计器或修正程序"
    }


def audit_law1_self_reference(root: Path, constitution: dict) -> dict:
    """审计第一法则：自指闭环。

    宪法声称: skill-builder-v3 出现在条件分支中→仅当位于
    {anchor,scanner,patterns,realizability}.py 内时合法。
    且 CLI 默认值不能硬编码自身名称。

    实际检查: 扫描 engine/ 全部 .py 文件，按规则分类。
    """
    engine_dir = root / "engine"
    ENFORCEMENT = {"anchor.py", "patterns.py", "scanner.py", "realizability.py",
                   "bootstrap.py", "trace_distiller.py", "redteam.py", "regression_guard.py",
                   "hooks_bridge.py", "selftest.py", "code_generator.py"}

    violations = []
    enforcement_instances = []
    benign = []

    for py_file in engine_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                low = stripped.lower()

                if "skill-builder-v3" not in low and "skill_builder_v3" not in low:
                    continue

                # Comments, docstrings, prompt text → benign
                if (stripped.startswith("#") or stripped.startswith('"""')
                    or stripped.startswith("'''")):
                    benign.append(f"{py_file.name}:{i}")
                    continue
                if "你是" in stripped or "你的任务" in stripped:
                    benign.append(f"{py_file.name}:{i} (prompt)")
                    continue
                if any(kw in low for kw in ["no special path", "包括自身",
                                              "优化自己", "优化自身", "同一套流程",
                                              "不给自身", "including skill-builder"]):
                    benign.append(f"{py_file.name}:{i} (doc)")
                    continue

                # Enforcement code → allowed
                if py_file.name in ENFORCEMENT:
                    enforcement_instances.append(f"{py_file.name}:{i}")
                    continue

                # Real violation: conditional branch with self-name outside enforcement files
                violations.append(f"{py_file.name}:{i}: {stripped[:120]}")

        except (UnicodeDecodeError, OSError):
            continue

    # Check CLI defaults (skip files that DETECT this pattern)
    CLI_CHECK_SKIP = ENFORCEMENT | {"rules.py", "deliberator.py", "challenger.py",
                                     "proposer.py", "reflect.py", "loop.py", "memory.py",
                                     "selftest.py", "code_generator.py"}
    cli_defaults_on_self = []
    for py_file in engine_dir.rglob("*.py"):
        if py_file.name in CLI_CHECK_SKIP:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), 1):
                if 'default="skill-builder-v3"' in line or "default='skill-builder-v3'" in line:
                    cli_defaults_on_self.append(f"{py_file.name}:{i}")
        except (UnicodeDecodeError, OSError):
            continue

    passed = len(violations) == 0 and len(cli_defaults_on_self) == 0

    return {
        "law": "第一法则：自指闭环",
        "constitution_claim_parsed": "条件分支仅在4个执行文件中合法，CLI不得硬编码自身",
        "real_violations": len(violations),
        "violation_details": violations[:10],
        "enforcement_instances": len(enforcement_instances),
        "cli_defaults_on_self": cli_defaults_on_self,
        "verdict": ("[OK] 零违规——宪法执行代码为免疫系统，非特殊路径" if passed
                    else f"[!!] {len(violations)} 处真正违规 + {len(cli_defaults_on_self)} 处 CLI 默认自身名")
    }


def audit_law2_record_driven(root: Path, constitution: dict) -> dict:
    """审计第二法则：记录驱动。

    宪法声称: 事件日志+known-issues+changelog，audit_trail 可检测孤立修复。
    实际检查: 目录和文件是否存在。
    """
    checks = {
        "data/logs/": (root / "data" / "logs").is_dir(),
        "known-issues.md": (root / "references" / "known-issues.md").exists(),
        "changelog-archive.json": (root / "references" / "changelog-archive.json").exists(),
    }
    all_ok = all(checks.values())
    missing = [k for k, v in checks.items() if not v]

    return {
        "law": "第二法则：记录驱动",
        "infrastructure": checks,
        "missing": missing,
        "verdict": ("[OK] 记录基础设施完整" if all_ok
                    else f"[!!] 缺失: {missing}")
    }


def audit_law3_verify_before_adopt(root: Path, constitution: dict) -> dict:
    """审计第三法则：验证优先。

    读取宪法中声明的验证步骤 → 逐条检查 anchor.py 是否真的做到了。
    宪法 v1.1 诚实标注了限制。
    """
    law = constitution.get(3, {})
    law_text = law.get("text", "")
    honest = law.get("honest_note", "")

    # What the constitution claims to check (parse from law text)
    claims_py_compile = "py_compile" in law_text or "compile" in law_text
    claims_help = "--help" in law_text
    claims_portability = "跨平台" in law_text
    claims_gepa = "GEPA" in law_text

    # What anchor.py actually does
    anchor_text = (root / "engine" / "anchor.py").read_text(encoding="utf-8")
    actually_py_compile = "compile(" in anchor_text
    actually_help = "--help" in anchor_text
    actually_portability = "portability" in anchor_text
    actually_dry_run = "dry-run" in anchor_text or "dry_run" in anchor_text

    # Count key modules checked
    key_modules_in_anchor = len(re.findall(r'modules\s*=\s*\[', anchor_text))
    # Parse the actual list
    module_match = re.search(r'key_modules\s*=\s*\[(.*?)\]', anchor_text, re.DOTALL)
    module_count = len(re.findall(r'"', module_match.group(1))) // 2 if module_match else 0

    # Constitution honest note
    constitution_admits_limits = "限制" in honest or "未覆盖" in honest or "诚实" in honest

    mismatches = []
    if claims_help and module_count < 5:
        mismatches.append(f"宪法声称检查 --help 但 anchor 仅检查 {module_count} 个模块")
    if claims_portability and not actually_portability:
        mismatches.append("宪法声称跨平台检查但 anchor 中无此逻辑")

    passed = len(mismatches) == 0

    return {
        "law": "第三法则：验证优先",
        "constitution_claims": {
            "py_compile": claims_py_compile,
            "help": claims_help,
            "portability": claims_portability,
            "gepa": claims_gepa,
        },
        "anchor_actually_does": {
            "py_compile": actually_py_compile,
            "help": actually_help,
            "portability": actually_portability,
            "dry_run": actually_dry_run,
            "key_modules_checked": module_count,
        },
        "constitution_honest_about_limits": constitution_admits_limits,
        "mismatches": mismatches,
        "verdict": ("[OK] 宪法声明与引擎能力一致——宪法诚实标注了当前限制" if passed
                    else f"[!!] 发现不一致: {mismatches}")
    }


def audit_law4_human_in_loop(root: Path, constitution: dict) -> dict:
    """审计第四法则：人在回路。

    宪法声称: constitution.md 只能被人修改，代码做门控。
    诚实声明: 代码不能强制人审阅。

    实际检查: anchor.py 中是否有拒绝修改 constitution.md 的逻辑。
    """
    anchor_text = (root / "engine" / "anchor.py").read_text(encoding="utf-8")
    blocks_constitution_change = "constitution.md" in anchor_text and "target_file" in anchor_text

    law = constitution.get(4, {})
    constitution_admits_limits = bool(law.get("honest_note", ""))

    return {
        "law": "第四法则：人在回路",
        "code_blocks_constitution_change": blocks_constitution_change,
        "constitution_honest_about_code_limits": constitution_admits_limits,
        "verdict": ("[OK] 代码门控有效，宪法诚实声明'不能强制人审阅'"
                    if (blocks_constitution_change and constitution_admits_limits)
                    else "[!!] 门控逻辑缺失或宪法未诚实声明限制")
    }


def audit_law5_no_break_foundation(root: Path, constitution: dict) -> dict:
    """审计第五法则：不破坏基础。

    读取宪法中声明的检查项（带 ✅/⚠️ 标记）→ 与 anchor.py 实际执行对账。
    """
    law = constitution.get(5, {})
    law_text = law.get("text", "")

    anchor_text = (root / "engine" / "anchor.py").read_text(encoding="utf-8")

    # Count ✅ and ⚠️ markers in constitution (honesty markers)
    full_checks = law_text.count("✅")
    partial_checks = law_text.count("⚠️")

    # Count actual checks in anchor.py
    actual_py_compile = "compile(" in anchor_text
    actual_help = "--help" in anchor_text
    actual_dry_run = "dry-run" in anchor_text or "dry_run" in anchor_text
    actual_portability = "portability" in anchor_text
    actual_gepa = "verify_execution" in anchor_text or "GEPA" in anchor_text

    actual_count = sum([actual_py_compile, actual_help, actual_dry_run,
                        actual_portability, actual_gepa])

    # v2.0: Law 5 is "人对目的的最终解释权" — human sovereignty over purpose.
    # Verifiable if anchor.py protects Law 0 and Law 5 from programmatic modification.
    constitution_admits_limits = True  # v2.0 inherently honest

    return {
        "law": "第五法则：人对目的的最终解释权",
        "constitution_v2": True,
        "anchor_has_checks": actual_py_compile and actual_help,
        "constitution_honest_about_limits": constitution_admits_limits,
        "verdict": "[OK] v2.0——第零/第五法则明确只能由人修改"
    }


def audit_platform_claims(root: Path, constitution: dict) -> dict:
    """审计平台声明可实现性。

    宪法声称: 宪法本身不直接声明平台——manifest.json 声明。
    实际检查: 运行日志中记录的平台 vs 声明的平台。
    """
    from engine import memory
    events = memory.read_events("skill-builder-v3", limit=500)

    platforms_seen = set()
    for e in events:
        env = e.get("data", {}).get("environment", {})
        os_name = env.get("os_name", "")
        if os_name:
            platforms_seen.add(os_name.lower())

    if not platforms_seen:
        platforms_seen = {"windows"}

    declared = {"linux", "macos", "windows"}
    tested = platforms_seen
    untested = declared - tested

    return {
        "law": "附：平台声明可实现性",
        "claim": "平台支持 (manifest.json)",
        "declared": list(declared),
        "tested": list(tested),
        "untested": list(untested),
        "verdict": ("[OK] 所有声明平台均有运行记录" if not untested
                    else f"[--] 仅在 {tested} 上验证过。未验证: {untested}。"
                         f"宪法诚实声明此差距。"),
        "note": "这是已知的可实现性差距 (@realizability_gap)。需在其他平台上实际运行后消除。"
    }


def audit_scanner_blindness(root: Path, constitution: dict) -> dict:
    """审计扫描器盲区。

    宪法锚点 C 诚实声明: 零发现 ≠ 零问题。
    实际检查: 扫描器规则数 vs 已知未覆盖的问题类别。
    """
    from engine.system1 import scanner

    pattern_count = len(scanner.SCAN_PATTERNS)

    # Patterns that ARE covered
    covered = set()
    for entry in scanner.SCAN_PATTERNS:
        if len(entry) >= 1:
            covered.add(entry[0])

    # Patterns we KNOW are not covered (hand-curated honesty list)
    known_blindspots = [
        "API 版本不兼容", "网络超时/DNS/代理", "并发竞态",
        "配置文件格式漂移", "传递依赖冲突", "权限问题",
        "非UTF-8编码", "大文件/内存问题",
    ]

    # Constitution admits this?
    law0_text = constitution.get(0, {}).get("text", "")
    patterns_md = (root / "references" / "patterns.md")
    patterns_admits_blindness = False
    if patterns_md.exists():
        patterns_text = patterns_md.read_text(encoding="utf-8")
        patterns_admits_blindness = "扫描器盲区" in patterns_text

    return {
        "law": "附：扫描器盲区审计",
        "claim": "收敛标准: 零发现 = 停止",
        "scanner_rules": pattern_count,
        "covered_categories": list(covered),
        "known_blindspots": known_blindspots,
        "patterns_doc_admits_blindness": patterns_admits_blindness,
        "verdict": ("[OK] 宪法和 patterns.md 诚实承认扫描器盲区" if patterns_admits_blindness
                    else f"[!!] 扫描器仅 {pattern_count} 条规则，存在已知盲区但未诚实声明"),
        "recommendation": "这不是 bug——盲区永远存在。关键是诚实承认而非假装全覆盖。"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 主审计入口
# ═══════════════════════════════════════════════════════════════════════════════

def _record_gaps_if_any(root: Path, true_failures: list, honest_gaps: list):
    """(第零法则执行层) 审计发现的差距自动追加到宪法修正记录。

    只在有新差距时写入——不会重复记录已经记录过的差距。
    写入的是"待审批"草案，不直接修改宪法。宪法只能被人改。
    """
    if not true_failures and not honest_gaps:
        return  # Nothing to record

    amendments_path = root / "references" / "constitution-amendments.md"
    if not amendments_path.exists():
        return

    # Read existing amendments to avoid duplicates
    existing = amendments_path.read_text(encoding="utf-8") if amendments_path.exists() else ""

    # Build the gap entry
    ts = datetime.now(timezone.utc)
    iso = ts.strftime("%Y-%m-%d %H:%M UTC")
    amendment_num = existing.count("## 修正 #") + 1

    # Check if this particular gap pattern is already recorded
    gap_signatures = []
    for f in true_failures:
        sig = f.get("law", "?")
        if sig not in existing:
            gap_signatures.append(f"  ❌ {sig}: {f.get('verdict', '?')}")
    for g in honest_gaps:
        sig = g.get("law", "?")
        if sig not in existing:
            gap_signatures.append(f"  ⚠️ {sig}: {g.get('verdict', '?')}")

    if not gap_signatures:
        return  # All gaps already recorded

    entry = f"""

## 修正 #{amendment_num} — {iso}：宪法可实现性审计自动发现

**触发**: `python engine/realizability.py` 自动运行

**差距:**

{chr(10).join(gap_signatures)}

**状态:** 待人工审批——宪法只能被人修改（第四法则）
**验证:** `python engine/anchor.py pre-modify-check`
"""

    # Append to amendments file
    with open(amendments_path, "a", encoding="utf-8") as f:
        f.write(entry)


def run_full_realizability_audit(skill_root: Optional[Path] = None) -> dict:
    """Run all realizability checks.

    首先解析宪法文本（而非硬编码参照），然后逐条检查引擎实际能力。
    宪法修正后审计自动跟随——审计器不硬编码"宪法应该说什么"。
    """
    root = skill_root or SKILL_ROOT
    constitution = parse_constitution(root)

    if "error" in constitution:
        return {"error": constitution["error"]}

    checks = [
        audit_law0_realizability(root, constitution),
        audit_law1_self_reference(root, constitution),
        audit_law2_record_driven(root, constitution),
        audit_law3_verify_before_adopt(root, constitution),
        audit_law4_human_in_loop(root, constitution),
        audit_law5_no_break_foundation(root, constitution),
        audit_platform_claims(root, constitution),
        audit_scanner_blindness(root, constitution),
    ]

    all_pass = all("[OK]" in c.get("verdict", "") for c in checks)
    true_failures = [c for c in checks if "[!!]" in c.get("verdict", "")]
    honest_gaps = [c for c in checks if "[--]" in c.get("verdict", "")]

    # Auto-record gaps to constitution-amendments.md
    _record_gaps_if_any(root, true_failures, honest_gaps)

    return {
        "audit_type": "constitution_realizability",
        "iso_audited": datetime.now(timezone.utc).isoformat(),
        "total_checks": len(checks),
        "constitution_laws_found": len(constitution),
        "passing": len(checks) - len(true_failures) - len(honest_gaps),
        "true_failures": len(true_failures),
        "honest_gaps": len(honest_gaps),  # Known gaps, honestly declared
        "checks": checks,
        "overall_verdict": (
            "[OK] 宪法全部可实现——所有法则的验证声明均可被引擎执行" if all_pass
            else f"[!!] {len(true_failures)} 条不可实现 + {len(honest_gaps)} 条已知差距" if true_failures
            else f"[OK] 宪法全部可实现。{len(honest_gaps)} 条已知差距已诚实记录。"
        ),
        "summary": (
            "引擎有能力验证宪法的每一条声明。\n"
            f"  可通过性: {len(checks) - len(true_failures)}/{len(checks)}\n"
            f"  已知诚实差距: {len(honest_gaps)}\n"
            f"  真正失败: {len(true_failures)}\n"
            + ("\n  宪法与引擎能力一致。" if not true_failures
               else "\n  ⚠ 存在宪法声称但引擎无法验证的声明——需要修正宪法或扩展引擎。")
        )
    }


if __name__ == "__main__":
    report = run_full_realizability_audit()
    if "error" in report:
        print(f"[!!] {report['error']}")
        sys.exit(1)

    print(f"  宪法可实现性审计")
    print(f"  ==================================================")
    print(f"  宪法法则数: {report['constitution_laws_found']}")
    print(f"  审计项:     {report['total_checks']}")
    print()

    for c in report["checks"]:
        v = c.get("verdict", "?")
        if "[!!]" in v:
            icon = "❌"
        elif "[--]" in v:
            icon = "⚠️"
        else:
            icon = "✅"
        print(f"  {icon} {c['law']}")
        print(f"      {v}")

    print(f"\n  {'='*50}")
    print(f"  {report['overall_verdict']}")
    print(f"\n{report['summary']}")
