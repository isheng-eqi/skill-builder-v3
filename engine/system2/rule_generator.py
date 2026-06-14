#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 2: Rule Generator — 从执行轨迹蒸馏可执行修复规则。

v1.2 (2026-06-13): 蒸馏自三项前沿框架:
  - Gödel Agent (ACL 2025): LLM递归修改自身逻辑——规则不是预定义的，而是生成的
  - Socratic-SWE (ICLR 2026): 历史求解轨迹蒸馏为结构化agent技能
  - Yunjue Agent (arXiv 2026): 工具优先进化——从执行反馈生成验证工具

核心流程:
  1. 收集顽固模式 + execution traces + deliberation输出
  2. 为每个Unruly Pattern生成候选规则(含检测条件+修复策略)
  3. 规则进入试用期(probation=5)，需System 2审批每次试用修复
  4. 试用期满且成功率≥70% → 晋升为auto_apply=True
  5. 试用期失败≥3次 → 规则废弃

生成规则存储在 data/generated_rules/ 中，与硬编码RULES并行加载。
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
def _generated_rules_dir(skill_name: str) -> Optional[Path]:
    root = _find_skill_root(skill_name)
    if not root:
        return None
    d = root / "data" / "generated_rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Rule data structure ───────────────────────────────────────────────────────

def create_rule(name: str, pattern_name: str, pattern_regex: str,
                file_glob: str, severity: str, description: str,
                fix_strategy: str, source_traces: list,
                deliberation_insight: str = "",
                confidence: float = 0.5) -> dict:
    """Create a generated rule with probation metadata.

    Args:
        name: 唯一规则名(如 'fix-missing-import')
        pattern_name: 对应pattern名
        pattern_regex: 检测正则(Python re语法)
        file_glob: 文件匹配glob
        severity: critical|warning|info
        description: 问题描述
        fix_strategy: 修复策略的自然语言描述——LLM据此生成具体修复代码
        source_traces: 启发生成此规则的execution trace事件ID列表
        deliberation_insight: System 2 deliberation的insight ID
        confidence: 初始置信度(0-1)

    Returns a structured rule dict ready for serialization.
    """
    ts = datetime.now(timezone.utc)
    return {
        "rule_id": f"gen-{ts.strftime('%Y%m%d-%H%M%S')}-{name}",
        "name": name,
        "pattern": pattern_name,
        "pattern_regex": pattern_regex,
        "file_glob": file_glob,
        "severity": severity,
        "auto_apply": False,  # Always start in probation
        "description": description,
        "fix_strategy": fix_strategy,
        "source": {
            "type": "generated",
            "generator": "rule_generator.py",
            "iso_generated": ts.isoformat(),
            "deliberation_insight": deliberation_insight,
            "source_traces": source_traces,
            "confidence": confidence
        },
        "probation": {
            "active": True,
            "remaining": 5,        # 试用修复次数
            "total_required": 5,
            "successes": 0,
            "failures": 0,
            "history": []          # [{iso, success, evidence_span_id, verdict}]
        },
        "status": "probation"     # probation | active | deprecated
    }


def save_rule(skill_name: str, rule: dict) -> Optional[Path]:
    """Save a generated rule to data/generated_rules/<rule_id>.json."""
    gd = _generated_rules_dir(skill_name)
    if not gd:
        return None
    fp = gd / f"{rule['rule_id']}.json"
    fp.write_text(json.dumps(rule, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def load_all_generated_rules(skill_name: str) -> list:
    """Load all generated rules from disk. Newest first."""
    gd = _generated_rules_dir(skill_name)
    if not gd:
        return []
    rules = []
    for fp in sorted(gd.glob("gen-*.json"), reverse=True):
        try:
            rule = json.loads(fp.read_text(encoding="utf-8"))
            rules.append(rule)
        except (json.JSONDecodeError, OSError):
            continue
    return rules


def load_rule(skill_name: str, rule_id: str) -> Optional[dict]:
    """Load a specific generated rule by ID."""
    gd = _generated_rules_dir(skill_name)
    if not gd:
        return None
    fp = gd / f"{rule_id}.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def get_active_rules(skill_name: str) -> list:
    """Get all generated rules that are active (auto_apply=True, not deprecated)."""
    all_rules = load_all_generated_rules(skill_name)
    return [r for r in all_rules
            if r.get("auto_apply", False) and r.get("status") != "deprecated"]


def get_probation_rules(skill_name: str) -> list:
    """Get all generated rules still in probation."""
    all_rules = load_all_generated_rules(skill_name)
    return [r for r in all_rules if r.get("status") == "probation"]


# ── Rule lifecycle ────────────────────────────────────────────────────────────

def record_probation_fix(skill_name: str, rule_id: str,
                          success: bool, evidence_span_id: str = "",
                          verdict: str = "") -> Optional[dict]:
    """Record a probation fix outcome for a generated rule.

    Called after each fix applied by a probation rule.
    Tracks success/failure and auto-promotes/deprecates when thresholds are met.
    """
    rule = load_rule(skill_name, rule_id)
    if not rule:
        return {"error": f"规则 '{rule_id}' 不存在"}

    prob = rule.get("probation", {})
    if not prob.get("active", False):
        return {"error": f"规则 '{rule_id}' 不在试用期"}

    # Record this fix
    entry = {
        "iso": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "evidence_span_id": evidence_span_id,
        "verdict": verdict
    }
    prob.setdefault("history", []).append(entry)

    if success:
        prob["successes"] = prob.get("successes", 0) + 1
    else:
        prob["failures"] = prob.get("failures", 0) + 1

    prob["remaining"] = max(0, prob.get("remaining", 5) - 1)

    # Check promotion: probation complete AND success rate >= 70%
    total_required = prob.get("total_required", 5)
    total_done = prob["successes"] + prob["failures"]
    success_rate = prob["successes"] / max(1, total_done)

    if total_done >= total_required and success_rate >= 0.7:
        rule["auto_apply"] = True
        rule["status"] = "active"
        prob["active"] = False
        rule["_promotion_note"] = (
            f"试用期通过: {prob['successes']}/{total_done} 成功 "
            f"({success_rate:.0%}), 于 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        )
        # v1.3: 自进化闭环——晋升规则注册到活跃规则集
        # 这是"优化别人的能力等于优化自己的能力"的物理实现
        try:
            from engine.system1 import rules as rules_mod
            rules_mod.register_promoted_rule(skill_name, rule)
            rule["_persist_to_active_set"] = True
        except ImportError:
            rule["_persist_to_active_set"] = False  # Will retry next time

    # Check deprecation: 3+ failures before completing probation
    if prob["failures"] >= 3 and prob["remaining"] > 0:
        rule["status"] = "deprecated"
        prob["active"] = False
        rule["_deprecation_note"] = (
            f"试用期废弃: {prob['failures']} 次失败未满 {total_required} 次试验, "
            f"于 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        )

    rule["probation"] = prob
    save_rule(skill_name, rule)

    # Record event
    from engine import memory
    memory.write_event(skill_name, "rule_generated", {
        "rule_id": rule_id,
        "rule_name": rule["name"],
        "action": "probation_fix",
        "success": success,
        "new_status": rule["status"],
        "probation_remaining": prob.get("remaining", 0)
    })

    return {
        "rule_id": rule_id,
        "success": success,
        "new_status": rule["status"],
        "auto_apply": rule.get("auto_apply", False),
        "probation": {
            "successes": prob["successes"],
            "failures": prob["failures"],
            "remaining": prob["remaining"]
        }
    }


def deprecate_rule(skill_name: str, rule_id: str, reason: str = "") -> bool:
    """Manually deprecate a generated rule."""
    rule = load_rule(skill_name, rule_id)
    if not rule:
        return False
    rule["status"] = "deprecated"
    rule["auto_apply"] = False
    if rule.get("probation", {}):
        rule["probation"]["active"] = False
    rule["_deprecation_reason"] = (
        reason or f"手动废弃于 {datetime.now(timezone.utc).isoformat()}"
    )
    save_rule(skill_name, rule)
    return True


# ── Rule generation from patterns ─────────────────────────────────────────────

def generate_rule_from_stubborn_pattern(
    skill_name: str,
    pattern_name: str,
    pattern_count: int,
    affected_files: list,
    related_traces: list,
    deliberation_text: str = "",
    known_fix_patterns: Optional[dict] = None
) -> Optional[dict]:
    """Generate a candidate rule for a stubborn pattern.

    This is the core Gödel Agent distillation: take a pattern that System 1
    can detect but cannot fix, and generate a potential fix strategy.

    The fix_strategy is a natural language description that the LLM
    (when called as part of System 2) uses to write the actual fix code.
    The generated rule enters probation immediately.

    Args:
        skill_name: 目标skill
        pattern_name: 顽固模式名
        pattern_count: 出现次数
        affected_files: 受影响文件列表
        related_traces: 相关execution trace事件
        deliberation_text: System 2 deliberation的文本
        known_fix_patterns: 已知修复模式映射(从patterns.md提取)
    """
    # Determine file_glob from affected files
    file_glob = "*.py"
    extensions = set()
    for f in affected_files:
        ext = Path(f).suffix
        if ext:
            extensions.add(ext)
    if extensions:
        ext_list = sorted(extensions)
        if len(ext_list) == 1:
            file_glob = f"*{ext_list[0]}"
        else:
            file_glob = f"*{{{','.join(e.lstrip('.'))}}}"

    # Determine severity from trace analysis
    severity = "warning"
    failed_traces = [t for t in related_traces
                     if not t.get("data", {}).get("success", True)]
    if len(failed_traces) >= 3:
        severity = "critical"

    # Generate fix strategy based on pattern category
    fix_strategy = _infer_fix_strategy(pattern_name, affected_files,
                                        deliberation_text, known_fix_patterns)

    rule_name = f"fix-{pattern_name.replace(' ', '-').lower()[:40]}"

    rule = create_rule(
        name=rule_name,
        pattern_name=pattern_name,
        pattern_regex="",  # Use existing scanner pattern; this rule REPLACES/EXTENDS it
        file_glob=file_glob,
        severity=severity,
        description=f"自动生成规则——{pattern_name} 模式在过去 {pattern_count} 次扫描中反复出现",
        fix_strategy=fix_strategy,
        source_traces=[t.get("event_id", "?") for t in related_traces[:5]],
        deliberation_insight=deliberation_text,
        confidence=min(0.3 + pattern_count * 0.1, 0.8)
    )

    save_rule(skill_name, rule)

    # Record event
    from engine import memory
    memory.write_event(skill_name, "rule_generated", {
        "rule_id": rule["rule_id"],
        "rule_name": rule_name,
        "pattern_name": pattern_name,
        "action": "generated",
        "severity": severity,
        "pattern_count": pattern_count,
        "confidence": rule["source"]["confidence"]
    })

    return rule


def _infer_fix_strategy(pattern_name: str, affected_files: list,
                         deliberation_text: str = "",
                         known_fix_patterns: Optional[dict] = None) -> str:
    """Infer a fix strategy from pattern analysis.

    This is a heuristic—the real fix code is generated by the LLM during
    System 2 execution. This function provides the strategy blueprint.
    """
    pattern_lower = pattern_name.lower()

    # Pattern → strategy mappings
    strategy_map = {
        "language-drift": (
            "扫描受影响文件中出现的英文哨兵词(error/warning/failed/passed等)，"
            "将用户可见的输出消息替换为对应的中文表述。"
            "注意：仅替换用户可见输出，不替换代码关键字和变量名。"
        ),
        "hardcoded-numbers": (
            "将文档中硬编码的数字计数替换为描述性文本。"
            "例如 '3个步骤' → '全部步骤'，使得数字在代码变更后不会过时。"
            "保留代码中的数值常量——仅修改文档(.md)中的数字。"
        ),
        "hardcoded-versions": (
            "将硬编码版本约束替换为从manifest.json或frontmatter动态读取。"
            "对于无法动态读取的场景，至少添加注释标注需要同步更新的位置。"
        ),
        "dev-null": (
            "将 /dev/null 替换为跨平台等价写法: 使用 Python 时用 os.devnull，"
            "使用 shell 时检查是否为 Windows 环境，用 NUL 替代。"
        ),
        "missing-docstring": (
            "检查Python脚本开头是否有模块级docstring。"
            "如缺失，添加简洁的模块用途说明。"
        ),
        "missing-utf8-header": (
            "在 #!/usr/bin/env python3 后添加 import sys + "
            "sys.stdout.reconfigure(encoding='utf-8')。"
        ),
    }

    for key, strategy in strategy_map.items():
        if key in pattern_lower:
            return strategy

    # Generic strategy for unknown patterns
    files_summary = ", ".join(affected_files[:5])
    return (
        f"自动生成的修复策略(待LLM细化):\n"
        f"模式: {pattern_name}\n"
        f"受影响文件: {files_summary}\n"
        + (f"Deliberation分析: {deliberation_text[:300]}\n" if deliberation_text else "")
        + "修复步骤:\n"
        f"1. 分析 {pattern_name} 的根本原因\n"
        f"2. 确定最小修改范围\n"
        f"3. 编写修复代码\n"
        f"4. 运行 verification 命令确认修复有效\n"
    )


# ── Integration with rules.py ─────────────────────────────────────────────────

def merge_generated_rules(skill_name: str, hardcoded_rules: list) -> list:
    """Merge generated rules into the active rules list.

    Active generated rules (auto_apply=True, not deprecated) are appended
    to the rules list. Probation rules are appended but with auto_apply=False.

    v1.3: This works with SEED_RULES from rules.py. Promoted rules are now
    persisted via rules.register_promoted_rule() and loaded via
    rules.get_active_rules(). This function handles probation-only rules.

    (v1.2) Dynamically loads generated fix functions from .py files via
    code_generator.load_generated_fix().

    Returns the merged list.
    """
    generated = load_all_generated_rules(skill_name)

    merged = list(hardcoded_rules)  # Copy
    for rule in generated:
        if rule.get("status") == "deprecated":
            continue

        # (v1.2) Try to load generated fix function
        fix_fn = None
        code_path = rule.get("_generated_code_path", "")
        if code_path:
            try:
                from engine.system2.code_generator import load_generated_fix
                fix_fn = load_generated_fix(rule)
            except ImportError:
                pass  # TODO: log or re-raise

        # Convert generated rule to RULES format
        converted = {
            "name": rule["name"],
            "pattern": rule["pattern"],
            "severity": rule["severity"],
            "auto_apply": rule.get("auto_apply", False),
            "description": rule.get("description", ""),
            "_source": "generated",
            "_rule_id": rule["rule_id"],
            "_fix_strategy": rule.get("fix_strategy", ""),
            "_probation": rule.get("probation", {}),
            "_needs_code": fix_fn is None,  # v1.2: flag for code_generator
        }
        if fix_fn:
            converted["fix"] = fix_fn  # v1.2: attach generated function
        merged.append(converted)

    return merged


# ── Stats ──────────────────────────────────────────────────────────────────────

def rule_generation_stats(skill_name: str) -> dict:
    """Get statistics about generated rules."""
    all_rules = load_all_generated_rules(skill_name)
    active = [r for r in all_rules if r.get("status") == "active"]
    probation = [r for r in all_rules if r.get("status") == "probation"]
    deprecated = [r for r in all_rules if r.get("status") == "deprecated"]

    total_fixes_by_generated = sum(
        len(r.get("probation", {}).get("history", []))
        for r in all_rules
    )

    return {
        "total_generated": len(all_rules),
        "active": len(active),
        "probation": len(probation),
        "deprecated": len(deprecated),
        "total_fixes_by_generated": total_fixes_by_generated,
        "promotion_rate": len(active) / max(1, len(all_rules)),
        "active_rule_names": [r["name"] for r in active],
        "probation_rule_names": [r["name"] for r in probation]
    }


# ── (A) Rule aging — 对抗规则累积熵增 ──────────────────────────────────────

def _last_hit_round(skill_name: str, rule_pattern: str) -> int:
    """Find how many scan-events back a pattern was last seen."""
    from engine import memory
    events = memory.read_events(skill_name, limit=500)
    scan_events = [e for e in events if e["event_type"] == "scan"]
    for idx, e in enumerate(scan_events):
        for f in e.get("data", {}).get("findings", []):
            if f.get("pattern") == rule_pattern:
                return idx
    return -1


def age_rules(skill_name: str, dormant_threshold: int = 50,
               deprecated_threshold: int = 100) -> dict:
    """Remove unused rules. 50-round no-hit = dormant. 100 = deprecated."""
    from engine import memory

    all_rules = load_all_generated_rules(skill_name)
    events = memory.read_events(skill_name, limit=500)
    scan_count = len([e for e in events if e["event_type"] == "scan"])
    actions, dormant, deprecated = [], 0, 0

    for rule in all_rules:
        if rule.get("status") == "deprecated":
            continue
        last = _last_hit_round(skill_name, rule.get("pattern", ""))
        if last < 0:
            last = scan_count

        if last >= deprecated_threshold:
            rule["status"] = "deprecated"
            rule["auto_apply"] = False
            if rule.get("probation", {}):
                rule["probation"]["active"] = False
            rule["_aging_note"] = f"老化废弃: {last}轮未命中"
            save_rule(skill_name, rule)
            root = _find_skill_root(skill_name)
            if root:
                dp = root / "references" / "patterns-deprecated.md"
                with open(dp, "a", encoding="utf-8") as f:
                    f.write(f"\n---\n## {rule['name']}\n- 废弃: {last}轮未命中\n- 原模式: {rule.get('pattern','?')}\n- 规则ID: {rule.get('rule_id','?')}\n")
            memory.write_event(skill_name, "rule_generated", {
                "rule_id": rule["rule_id"], "rule_name": rule["name"],
                "action": "aged_deprecated", "rounds_since_hit": last
            })
            deprecated += 1
            actions.append(f"废弃: {rule['name']} ({last}轮未命中)")
        elif last >= dormant_threshold and rule.get("status") == "active":
            rule["_dormant"] = True
            save_rule(skill_name, rule)
            dormant += 1
            actions.append(f"休眠: {rule['name']} ({last}轮未命中)")
        elif last < dormant_threshold and rule.get("_dormant"):
            rule.pop("_dormant", None)
            save_rule(skill_name, rule)
            actions.append(f"激活: {rule['name']} (模式回归)")

    return {
        "dormant": dormant, "deprecated": deprecated,
        "checked": len(all_rules), "actions": actions,
        "verdict": f"[OK] {dormant}休眠 {deprecated}废弃" if actions else "[OK] 无需老化"
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="System 2 规则生成器(Gödel Agent)——从执行轨迹蒸馏可执行修复规则"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("generate", help="为顽固模式生成候选规则")
    p.add_argument("skill", help="目标skill名称")
    p.add_argument("--pattern", required=True, help="顽固模式名")
    p.add_argument("--count", type=int, default=3, help="出现次数")
    p.add_argument("--files", default="[]", help="JSON 受影响文件列表")
    p.add_argument("--traces", default="[]", help="JSON 相关trace事件ID列表")
    p.add_argument("--deliberation", default="", help="Deliberation文本")

    p = sub.add_parser("list", help="列出所有生成规则")
    p.add_argument("skill", help="目标skill名称")
    p.add_argument("--status", choices=["probation", "active", "deprecated"],
                   help="按状态筛选")

    p = sub.add_parser("show", help="显示特定生成规则")
    p.add_argument("skill", help="目标skill名称")
    p.add_argument("rule_id", help="规则ID")

    p = sub.add_parser("deprecate", help="废弃生成规则")
    p.add_argument("skill", help="目标skill名称")
    p.add_argument("rule_id", help="规则ID")
    p.add_argument("--reason", default="", help="废弃原因")

    p = sub.add_parser("age", help="(A) 老化未使用的生成规则")
    p.add_argument("skill", help="目标skill名称")
    p.add_argument("--dormant", type=int, default=50, help="休眠阈值 (默认50)")
    p.add_argument("--deprecate-threshold", type=int, default=100,
                   help="废弃阈值 (默认100)")

    p = sub.add_parser("stats", help="规则生成统计")
    p.add_argument("skill", help="目标skill名称")

    args = parser.parse_args()

    if args.cmd == "generate":
        files = json.loads(args.files)
        traces = json.loads(args.traces)
        rule = generate_rule_from_stubborn_pattern(
            args.skill, args.pattern, args.count,
            files, traces, args.deliberation
        )
        if rule:
            print(f"[OK] 规则已生成: {rule['rule_id']}")
            print(f"  名称: {rule['name']}")
            print(f"  状态: {rule['status']} (试用剩余: {rule['probation']['remaining']})")
            print(f"  置信度: {rule['source']['confidence']:.0%}")
            print(f"  修复策略: {rule['fix_strategy'][:200]}")
        else:
            print("[!!] 规则生成失败")
            sys.exit(1)

    elif args.cmd == "list":
        rules = load_all_generated_rules(args.skill)
        if args.status:
            rules = [r for r in rules if r.get("status") == args.status]
        for r in rules:
            icons = {"active": "[✓]", "probation": "[?]", "deprecated": "[✗]"}
            icon = icons.get(r.get("status", ""), "[?]")
            print(f"  {icon} {r['rule_id']} [{r.get('severity', '?')}] "
                  f"{r.get('name', '?')}")
        if not rules:
            print("  (无生成规则)")

    elif args.cmd == "show":
        rule = load_rule(args.skill, args.rule_id)
        if rule:
            print(json.dumps(rule, indent=2, ensure_ascii=False))
        else:
            print(f"[!!] 规则不存在: {args.rule_id}")
            sys.exit(1)

    elif args.cmd == "deprecate":
        ok = deprecate_rule(args.skill, args.rule_id, args.reason)
        if ok:
            print(f"[OK] 规则已废弃: {args.rule_id}")
        else:
            print(f"[!!] 废弃失败: {args.rule_id}")
            sys.exit(1)

    elif args.cmd == "age":
        result = age_rules(args.skill, args.dormant, args.deprecate_threshold)
        print(f"  {result['verdict']}")
        for a in result.get("actions", []):
            print(f"    {a}")

    elif args.cmd == "stats":
        stats = rule_generation_stats(args.skill)
        print(f"  生成规则统计: {args.skill}")
        print(f"  总计: {stats['total_generated']}")
        print(f"  活跃: {stats['active']} | 试用: {stats['probation']} | 废弃: {stats['deprecated']}")
        print(f"  晋升率: {stats['promotion_rate']:.0%}")
        print(f"  累计修复: {stats['total_fixes_by_generated']}")
        if stats['active_rule_names']:
            print(f"  活跃规则: {stats['active_rule_names']}")
        if stats['probation_rule_names']:
            print(f"  试用规则: {stats['probation_rule_names']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
