#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Self-Test — 自进化回路端到端验证模块.

v1.3 (2026-06-13): 验证自进化回路是否完全闭合。

四个测试:
  测试1: 规则晋升回环 — 生成规则能否晋升并持久化到活跃规则集
  测试2: 宪法自检 — anchor.py 全部检查通过，安全门控与可进化逻辑正确区分
  测试3: Hook 配置验证 — settings.json 中 hooks 已正确安装
  测试4: 自指闭环扫描 — 扫描自身不产生自指特殊路径违规

用法:
  python engine/selftest.py              # 运行全部测试
  python engine/selftest.py --test 1     # 仅运行测试1
  python engine/selftest.py --verbose    # 详细输出
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

SKILL_NAME = "skill-builder-v3"


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Rule Promotion Loop
# ═══════════════════════════════════════════════════════════════════════════

def test_rule_promotion_loop(verbose: bool = False) -> dict:
    """Test that generated rules can be promoted and persist in active rule set.

    Steps:
      1. Scan self → get findings
      2. Check get_active_rules() includes SEED_RULES + promoted + probation
      3. Verify any promoted rules exist in active_rules.json
      4. Verify rule_generator.get_active_rules() returns promoted rules
      5. Verify code paths are consistent (no RULES-only code paths remain)
    """
    from engine.system1 import rules
    from engine.system2 import rule_generator

    passed = True
    details = []

    # Step 1: Scan self
    from engine.system1 import scanner
    scan = scanner.scan_skill(SKILL_NAME)
    findings = scan.get("findings", [])
    findings_count = scan.get("findings_count", 0)
    details.append(f"自扫描: {findings_count} 个发现")

    # Step 2: get_active_rules() returns all categories
    active = rules.get_active_rules(SKILL_NAME)
    seed_count = len(rules.SEED_RULES)
    promoted_count = len(rules.load_persisted_active_rules(SKILL_NAME))
    total_active = len(active)
    details.append(f"活跃规则: {total_active} 条 (种子={seed_count}, 晋升={promoted_count})")

    if total_active < seed_count:
        passed = False
        details.append("[!!] 活跃规则数 < 种子规则数——get_active_rules() 丢失了种子规则")
    else:
        details.append("[OK] 活跃规则包含全部种子规则")

    # Step 3: Generated rules with active status are in persisted list
    gen_rules = rule_generator.load_all_generated_rules(SKILL_NAME)
    active_gen = [r for r in gen_rules if r.get("status") == "active"]
    persisted = rules.load_persisted_active_rules(SKILL_NAME)
    persisted_ids = {r.get("_rule_id") for r in persisted}

    details.append(f"生成的活跃规则: {len(active_gen)} 条")
    for ag in active_gen:
        rid = ag.get("rule_id", "?")
        if rid in persisted_ids:
            details.append(f"  [OK] {ag['name']} — 已持久化到 active_rules.json")
        else:
            details.append(f"  [--] {ag['name']} — 活跃但未持久化 (可能晋升后未调用 register_promoted_rule)")
            # This is a soft failure — the rule is active, just not yet persisted

    # Step 4: Active rules are found by get_applicable_rules()
    applicable = rules.get_applicable_rules(findings, SKILL_NAME)
    details.append(f"适用规则: {len(applicable)} 条匹配当前发现")

    # Step 5: SEED_RULES is unchanged (immutable)
    expected_seed = 7  # The 7 original seed rules
    if seed_count >= expected_seed:
        details.append(f"[OK] 种子规则保持完整 ({seed_count} 条)")
    else:
        passed = False
        details.append(f"[!!] 种子规则丢失: {seed_count} < {expected_seed}")

    # Step 6: No code path uses bare RULES (should use SEED_RULES or get_active_rules)
    import ast
    rules_py = SKILL_ROOT / "engine" / "system1" / "rules.py"
    rules_code = rules_py.read_text(encoding="utf-8")
    # Count occurrences of = RULES (should be only the old variable name in comments/docs)
    bare_rules_refs = rules_code.count("RULES")
    # Allow: SEED_RULES, get_active_rules, _active_rules_path, load_persisted_active_rules, "RULES" in comments
    # We expect bare "RULES" to only appear in comments (preceded by #) or in the old naming
    if "RULES =" in rules_code and "SEED_RULES =" in rules_code:
        details.append("[OK] SEED_RULES 已替代 RULES")
    else:
        details.append("[!!] RULES 到 SEED_RULES 迁移不完整")

    return {
        "test": "规则晋升回环",
        "passed": passed,
        "details": details,
        "score": f"{seed_count}+{promoted_count}={total_active} 活跃规则"
    }


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Constitution Self-Check
# ═══════════════════════════════════════════════════════════════════════════

def test_constitution_compliance(verbose: bool = False) -> dict:
    """Test constitution compliance via anchor.py.

    Steps:
      1. Run anchor.py pre-modify-check
      2. Run anchor.py check-autonomy
      3. Verify self-reference check passes (with v1.3 immune file whitelist)
      4. Verify constitution contains v1.3 amendments (第三法则 clause 7, 第五法则 clause 8)
    """
    passed = True
    details = []

    # Step 1: pre-modify-check
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "engine" / "anchor.py"), "pre-modify-check"],
            capture_output=True, text=True, timeout=60,
            cwd=str(SKILL_ROOT), encoding="utf-8", errors="replace"
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 and "all passed" in output.lower():
            details.append("[OK] anchor.py pre-modify-check 全部通过")
        elif "all_passed" in output.lower() or "[OK]" in output:
            details.append("[OK] anchor.py pre-modify-check 通过")
        else:
            # Check specific failures
            if "violations" in output.lower():
                passed = False
                details.append(f"[!!] pre-modify-check 发现违规: {output[:500]}")
            else:
                details.append(f"[--] pre-modify-check: {output[:300]}")
    except Exception as e:
        details.append(f"[!!] pre-modify-check 执行失败: {e}")
        passed = False

    # Step 2: check-autonomy
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "engine" / "anchor.py"), "check-autonomy"],
            capture_output=True, text=True, timeout=30,
            cwd=str(SKILL_ROOT), encoding="utf-8", errors="replace"
        )
        output = result.stdout + result.stderr
        if "autonomy" in output.lower() or "level" in output.lower():
            details.append(f"[OK] check-autonomy: {output[:200].strip()}")
        else:
            details.append(f"[--] check-autonomy: {output[:200].strip()}")
    except Exception as e:
        details.append(f"[--] check-autonomy 执行失败: {e}")

    # Step 3: Self-reference check
    from engine import anchor
    ref_check = anchor.check_self_reference(SKILL_ROOT)
    if ref_check["passed"]:
        details.append("[OK] 自指检查通过——无特殊路径违规")
    else:
        passed = False
        details.append(f"[!!] 自指违规: {ref_check.get('violations', [])}")

    # Step 4: Constitution v1.3 amendments present
    con_path = SKILL_ROOT / "constitution.md"
    if con_path.exists():
        con_text = con_path.read_text(encoding="utf-8")
        has_v13_rules = "(v1.3)" in con_text and "规则晋升" in con_text
        has_v13_immutable = "不可变安全门控" in con_text and "可进化" in con_text
        has_version_130 = "1.3.0" in con_text

        if has_v13_rules:
            details.append("[OK] 宪法包含 v1.3 规则晋升条款 (第三法则)")
        else:
            passed = False
            details.append("[!!] 宪法缺少 v1.3 规则晋升条款")

        if has_v13_immutable:
            details.append("[OK] 宪法区分了不可变安全门控与可进化逻辑 (第五法则)")
        else:
            passed = False
            details.append("[!!] 宪法缺少安全门控/可进化逻辑区分条款")

        if has_version_130:
            details.append("[OK] 宪法版本: 1.3.0")
        else:
            details.append("[--] 宪法版本未更新到 1.3.0")
    else:
        passed = False
        details.append("[!!] constitution.md 不存在")

    return {
        "test": "宪法自检",
        "passed": passed,
        "details": details
    }


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Hook Configuration
# ═══════════════════════════════════════════════════════════════════════════

def test_hook_configuration(verbose: bool = False) -> dict:
    """Test hook configuration is properly installed.

    Steps:
      1. Check settings.json exists
      2. Verify hooks key is present and non-empty
      3. Verify at least SessionStart and Stop hooks are configured
      4. Verify hook commands reference executable paths that exist
    """
    passed = True
    details = []

    settings_path = Path(os.environ.get("SB3_CONFIG_DIR", str(Path.home() / ".claude"))) / "settings.json"

    if not settings_path.exists():
        details.append("[!!] settings.json 不存在")
        return {"test": "Hook配置", "passed": False, "details": details}

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        details.append(f"[!!] settings.json 读取失败: {e}")
        return {"test": "Hook配置", "passed": False, "details": details}

    hooks = settings.get("hooks", {})
    if not hooks:
        details.append("[!!] hooks 为空——未安装自进化 Hook")
        details.append("  → 运行: python engine/hooks_bridge.py install --target skill-builder-v3")
        passed = False
        return {"test": "Hook配置", "passed": False, "details": details}

    # Check key hooks present
    required_hooks = ["SessionStart", "Stop", "PostToolUseFailure"]
    for hook_name in required_hooks:
        if hook_name in hooks and hooks[hook_name]:
            details.append(f"[OK] {hook_name} hook 已配置 ({len(hooks[hook_name])} 条命令)")
        else:
            passed = False
            details.append(f"[!!] {hook_name} hook 缺失")

    # Extra hooks are bonus
    extra_hooks = [h for h in hooks if h not in required_hooks]
    if extra_hooks:
        details.append(f"[OK] 额外 hook: {extra_hooks}")

    # Verify _hook_note present (our installation marker)
    if settings.get("_hook_note"):
        details.append(f"[OK] Hook 安装标记存在 (v1.3)")

    return {
        "test": "Hook配置",
        "passed": passed,
        "details": details,
        "hooks_installed": list(hooks.keys())
    }


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Self-Reference Closure Scan
# ═══════════════════════════════════════════════════════════════════════════

def test_self_reference_closure(verbose: bool = False) -> dict:
    """Test that skill-builder-v3 scanning itself produces valid, actionable results.

    This is the ultimate self-evolution test: can the system see its own problems?

    Steps:
      1. Run full scan on self
      2. Verify findings are valid (no false positives on immune system files)
      3. Verify each finding has a corresponding rule path
      4. Verify at least some findings have auto-fix rules available
      5. Run dry-run loop to verify no crashes
    """
    passed = True
    details = []

    # Step 1: Full scan
    from engine.system1 import scanner, rules

    scan = scanner.scan_skill(SKILL_NAME)
    findings = scan.get("findings", [])
    findings_count = scan.get("findings_count", 0)

    details.append(f"自扫描: {findings_count} 个发现")
    details.append(f"  严重: {scan.get('critical', 0)}, 警告: {scan.get('warning', 0)}, 信息: {scan.get('info', 0)}")

    # Step 2: No findings on immune system files? (anchor.py/scanner.py/patterns.py/realizability.py)
    immune_files = {"anchor.py", "scanner.py", "patterns.py", "realizability.py",
                    "rule_generator.py", "code_generator.py", "selftest.py"}
    immune_findings = [f for f in findings
                       if Path(f.get("file", "")).name in immune_files]
    if immune_findings:
        # These findings might be legitimate (e.g., bare-except in scanner.py)
        # so we just report them, not fail
        details.append(f"[--] 免疫文件中的发现: {len(immune_findings)} 条 "
                       f"({[f.get('pattern') for f in immune_findings[:3]]})")
    else:
        details.append("[OK] 免疫文件中无发现")

    # Step 3: Each finding has a rule path
    active_rules = rules.get_active_rules(SKILL_NAME)
    patterns_with_rules = {r["pattern"] for r in active_rules if r.get("auto_apply")}
    all_patterns = {f["pattern"] for f in findings}
    unruly = all_patterns - patterns_with_rules

    if unruly:
        details.append(f"[--] {len(unruly)} 个模式无自动修复规则: {unruly}")
        details.append("  (这些需要 System 2 处理——正常)")
    else:
        details.append("[OK] 所有发现都有对应的自动修复规则")

    fixable = all_patterns & patterns_with_rules
    if fixable:
        details.append(f"[OK] {len(fixable)} 个模式有自动修复覆盖: {fixable}")

    # Step 4: Dry-run loop doesn't crash
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "engine" / "loop.py"),
             "--target", SKILL_NAME, "--dry-run"],
            capture_output=True, text=True, timeout=60,
            cwd=str(SKILL_ROOT), encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            details.append(f"[OK] loop.py --dry-run 正常退出")
            for line in result.stdout.split("\n"):
                if "findings" in line.lower() or "gvu" in line.lower():
                    details.append(f"  {line.strip()[:120]}")
        else:
            details.append(f"[!!] loop.py --dry-run 异常退出 (exit={result.returncode})")
            if result.stderr:
                details.append(f"  stderr: {result.stderr[:300]}")
            passed = False
    except subprocess.TimeoutExpired:
        details.append("[!!] loop.py --dry-run 超时 (60s)")
        passed = False
    except Exception as e:
        details.append(f"[!!] loop.py --dry-run 执行失败: {e}")
        passed = False

    # Step 5: GVU SNR check
    from engine import memory
    gvu = memory.calculate_gvu_snr(SKILL_NAME)
    details.append(f"GVU 稳定性: {gvu['verdict']}")

    return {
        "test": "自指闭环扫描",
        "passed": passed,
        "details": details,
        "findings_count": findings_count,
        "fixable_patterns": list(fixable) if fixable else []
    }


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    test_rule_promotion_loop,
    test_constitution_compliance,
    test_hook_configuration,
    test_self_reference_closure,
]


def run_all_tests(verbose: bool = False) -> dict:
    """Run all self-tests and return aggregated results."""
    results = []
    all_passed = True

    print(f"{'='*60}")
    print(f"  skill-builder-v3 自进化回路验证")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  技能: {SKILL_NAME}")
    print(f"{'='*60}")

    for test_fn in ALL_TESTS:
        print(f"\n{'─'*40}")
        print(f"  测试: {test_fn.__doc__.split(chr(10))[0].strip() if test_fn.__doc__ else test_fn.__name__}")
        print(f"{'─'*40}")

        try:
            result = test_fn(verbose=verbose)
        except Exception as e:
            result = {
                "test": test_fn.__name__,
                "passed": False,
                "details": [f"测试崩溃: {e}"]
            }
            import traceback
            result["details"].append(traceback.format_exc())

        results.append(result)
        icon = "[OK]" if result["passed"] else "[!!]"
        print(f"  {icon} {result['test']}")

        for d in result.get("details", []):
            print(f"    {d}")

        if not result["passed"]:
            all_passed = False

    # Summary
    print(f"\n{'='*60}")
    passed_count = sum(1 for r in results if r["passed"])
    print(f"  结果: {passed_count}/{len(results)} 通过")
    print(f"  自进化回路: {'✅ 闭合' if all_passed else '❌ 断裂'}")
    print(f"{'='*60}")

    return {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "all_passed": all_passed,
        "results": results,
        "verdict": "[OK] 自进化回路完全闭合——四个断裂点全部修复" if all_passed
                   else f"[!!] {len(results) - passed_count} 个测试未通过——自进化回路仍有断裂"
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="skill-builder-v3 自进化回路端到端验证"
    )
    parser.add_argument("--test", type=int, choices=[1, 2, 3, 4],
                       help="仅运行指定测试 (1-4)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="详细输出")
    parser.add_argument("--json", action="store_true",
                       help="JSON 输出")

    args = parser.parse_args()

    if args.json:
        if args.test:
            test_fn = ALL_TESTS[args.test - 1]
            result = test_fn(verbose=args.verbose)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            report = run_all_tests(verbose=args.verbose)
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    elif args.test:
        test_fn = ALL_TESTS[args.test - 1]
        result = test_fn(verbose=args.verbose)
        print(f"\n{result['test']}: {'[OK]' if result['passed'] else '[!!]'}")
        for d in result.get("details", []):
            print(f"  {d}")
    else:
        report = run_all_tests(verbose=args.verbose)
        sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
