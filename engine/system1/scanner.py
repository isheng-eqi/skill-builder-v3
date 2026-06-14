#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 1: Fast scanner — grep/regex pattern detection.

Scans a skill's files for known anti-patterns. Returns structured findings.
Runs in seconds, no LLM needed. This is the "System 1" fast path.
"""

import re
from pathlib import Path
from typing import Optional

# ── Pattern definitions ───────────────────────────────────────────────────────

# Each pattern: (name, severity, regex, file_glob, description)
# severity: "critical" | "warning" | "info"

SCAN_PATTERNS = [
    # Pattern 7: Language consistency drift
    # v4 fix: removed scripts/*.py entry — tool scripts are internal, not user-facing.
    # English sentinel words in print() messages of tool scripts are legitimate.
    # Language consistency rule should only apply to user-facing documentation.
    # If needed, add specific .md glob patterns below.
    # (
    #     "language-drift",
    #     "warning",
    #     re.compile(r"(?i)(error|warning|failed|passed|check|not.?found|approved|rejected|pending|available|all.?clear|success|fail)\s*[:\-]", re.I),
    #     "scripts/*.py",
    #     "检测到英文哨兵词——可能违反中文一致性",
    # ),
    # Pattern 2: Hardcoded numbers in docs
    # (?<![\d\~\+]) excludes ~18 and 19+ (approximate markers)
    # (?!\+) excludes numbers followed by + (lower-bound ranges)
    (
        "hardcoded-numbers",
        "info",
        re.compile(r'(?<![\d\~\+])\d+(?!\+)\s*(项|条|个|步|轮)', re.I),
        "*.md",
        "文档中包含硬编码的数字计数——可能在代码变更后过时",
    ),
    # Pattern 6: Scattered implementations
    # (?!.*TODO) excludes lines already annotated by tag-hardcoded-versions fix
    (
        "hardcoded-versions",
        "warning",
        re.compile(r'(python|node)\s*(>=|<=|==|>|<)\s*[\d.]+(?!.*TODO)', re.I),
        "scripts/*.py",
        "硬编码版本约束——应从 manifest.json 或 frontmatter 动态读取",
    ),
    # Platform portability (exclude constitution.md — it discusses grep -P as anti-pattern)
    (
        "grep-pcre",
        "critical",
        re.compile(r'grep\s+.*-P', re.I),
        "*.md",
        "grep -P 不可移植（PCRE 不支持 BSD grep）",
        ["constitution.md"],
    ),
    (
        "dev-null",
        "warning",
        re.compile(r'/dev/null', re.I),
        "scripts/*.py",
        "使用了 /dev/null——在 Windows 上不可用（应使用 os.devnull 或 NUL）",
    ),
    # UTF-8 header check — for BOTH scripts/ and engine/
    (
        "missing-utf8-header",
        "critical",
        re.compile(r'^#!/usr/bin/env python3$', re.MULTILINE),
        "scripts/*.py",
        "检查 Python 脚本是否需要 UTF-8 stdout 头",
    ),
    # Same check for engine/ — enables self-detection of own problems
    (
        "missing-utf8-header",
        "critical",
        re.compile(r'^#!/usr/bin/env python3$', re.MULTILINE),
        "engine/*.py",
        "引擎模块缺少 UTF-8 stdout 头——自身免疫系统检测",
    ),
    # Same check for engine/system1/ and engine/system2/
    (
        "missing-utf8-header",
        "critical",
        re.compile(r'^#!/usr/bin/env python3$', re.MULTILINE),
        "engine/system1/*.py",
        "引擎子模块缺少 UTF-8 stdout 头",
    ),
    (
        "missing-utf8-header",
        "critical",
        re.compile(r'^#!/usr/bin/env python3$', re.MULTILINE),
        "engine/system2/*.py",
        "引擎子模块缺少 UTF-8 stdout 头",
    ),
    # Self-reference violations (exclude files that are SUPPOSED to check for self-reference)
    (
        "self-reference",
        "critical",
        re.compile(r'if\s+.*(skill.builder.v3|skill_builder_v3).*:', re.I),
        "engine/*.py",
        "检测到对 skill-builder-v3 的特殊路径分支——违反第一法则",
        ["anchor.py", "patterns.py", "scanner.py", "realizability.py", "bootstrap.py", "trace_distiller.py", "redteam.py", "regression_guard.py"],  # 免疫系统文件
    ),
    # Missing docstring
    (
        "missing-docstring",
        "info",
        re.compile(r'^#!/usr/bin/env python3\n(?!.*""")', re.MULTILINE),
        "scripts/*.py",
        "Python 脚本缺少 docstring",
    ),
    # ── (来自 skill-auditor 蒸馏) ──
    # bare-except: catches SystemExit/KeyboardInterrupt
    # (?!.*TODO) excludes lines already annotated by fix-bare-except fix
    # ^(?!\s*#) excludes comment lines — prevents false positives on docs describing the pattern
    (
        "bare-except",
        "warning",
        re.compile(r'^(?!\s*#).*except\s*:(?!.*TODO)', re.MULTILINE),
        "scripts/*.py",
        "裸 except: ——会吞 KeyboardInterrupt/SystemExit, 应指定具体异常类型",
    ),
    (
        "bare-except",
        "warning",
        re.compile(r'^(?!\s*#).*except\s*:(?!.*TODO)', re.MULTILINE),
        "engine/**/*.py",
        "裸 except: ——会吞 KeyboardInterrupt/SystemExit",
        ["scanner.py", "rules.py"],  # These DEFINE bare-except as anti-pattern
    ),
    # except: pass silently swallows all errors
    # (?!.*TODO) excludes lines already annotated by tag-except-pass fix
    # ^(?!\s*#) excludes comment lines (v4 fix: prevents false positives on comments describing the pattern)
    (
        "except-pass",
        "warning",
        re.compile(r'^(?!\s*#).*except\s+.*:\s*pass\s*$(?!.*TODO)', re.MULTILINE),
        "scripts/*.py",
        "except...pass 静默吞异常——至少应记录日志",
    ),
    (
        "except-pass",
        "warning",
        re.compile(r'^(?!\s*#).*except\s+.*:\s*pass\s*$(?!.*TODO)', re.MULTILINE),
        "engine/**/*.py",
        "except...pass 静默吞异常——至少应记录日志",
        ["scanner.py", "rules.py", "deep_audit.py"],  # These files DEFINE or describe except-pass as anti-pattern
    ),
    # skill.md 超过 500 行（渐进式披露）
    (
        "skill-md-oversize",
        "info",
        re.compile(r'.+'),  # 特殊处理: 检测行数而非内容匹配
        "skill.md",
        "skill.md 行数超过建议上限——考虑将深层内容移至 references/",
    ),
]


# ── File discovery ────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Scanner ───────────────────────────────────────────────────────────────────

def scan_skill(skill_name: str, patterns: Optional[list] = None) -> dict:
    """Run all scan patterns against a skill. Returns structured findings.

    Patterns come from TWO sources:
      1. Hardcoded SCAN_PATTERNS (8 base patterns)
      2. Generated rules with detection regexes (from rule_generator)

    This is the self-extending scanner: when rule_generator creates a new
    rule with a pattern_regex, the scanner automatically uses it next round.
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在", "findings": []}

    patterns = list(patterns or SCAN_PATTERNS)

    # Load generated patterns from rule_generator
    try:
        from engine.system2 import rule_generator
        gen_rules = rule_generator.load_all_generated_rules(skill_name)
        for rule in gen_rules:
            if rule.get("status") == "deprecated" or rule.get("_dormant"):
                continue
            preg = rule.get("pattern_regex", "")
            if not preg:
                continue
            try:
                compiled = re.compile(preg, re.I)
            except re.error:
                continue
            severity = rule.get("severity", "warning")
            file_glob = rule.get("file_glob", "*.py")
            desc = rule.get("description", rule.get("name", "generated"))
            patterns.append((rule["pattern"], severity, compiled, file_glob, desc))
    except ImportError:
        pass

    patterns = patterns or SCAN_PATTERNS
    findings = []

    for entry in patterns:
        # Unpack with optional exclusion list (patterns can have 5 or 6 elements)
        if len(entry) == 6:
            name, severity, regex, file_glob, description, exclude_files = entry
        else:
            name, severity, regex, file_glob, description = entry
            exclude_files = []

        for fp in root.glob(file_glob):
            # Skip excluded files
            if fp.name in exclude_files:
                continue
            if not fp.is_file():
                continue
            try:
                content = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            rel_path = fp.relative_to(root)

            # Special handling: missing-utf8-header is about what's NOT there
            if name == "missing-utf8-header":
                if fp.suffix == ".py" and "sys.stdout.reconfigure" not in content:
                    findings.append({
                        "pattern": name,
                        "severity": severity,
                        "file": str(rel_path),
                        "line": 1,
                        "description": "Python 脚本缺少 UTF-8 stdout 头",
                        "match": fp.name
                    })
                continue

            # Special handling: skill-md-oversize counts lines
            if name == "skill-md-oversize":
                line_count = len(content.split("\n"))
                if line_count > 500:
                    findings.append({
                        "pattern": name,
                        "severity": severity,
                        "file": str(rel_path),
                        "line": 1,
                        "description": f"skill.md 共 {line_count} 行 (>500), 建议拆分深层内容至 references/",
                        "match": f"{line_count} lines"
                    })
                continue

            # Standard regex match
            for match in regex.finditer(content):
                line_num = content[:match.start()].count("\n") + 1
                findings.append({
                    "pattern": name,
                    "severity": severity,
                    "file": str(rel_path),
                    "line": line_num,
                    "description": description,
                    "match": match.group(0)[:100]
                })

    # ── Metric-based scan (v1.2): 模式12(指标死值)/13(累计倒退)/14(置信悬崖) ──
    metric_findings = _scan_metrics(skill_name, root)
    findings.extend(metric_findings)

    # Sort by severity
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (severity_order.get(f["severity"], 9), f["file"], f["line"]))

    return {
        "skill": skill_name,
        "findings_count": len(findings),
        "findings": findings,
        "by_severity": {
            "critical": len([f for f in findings if f["severity"] == "critical"]),
            "warning": len([f for f in findings if f["severity"] == "warning"]),
            "info": len([f for f in findings if f["severity"] == "info"]),
        }
    }


def scan_self() -> dict:
    """Scan skill-builder-v3 itself — with the same patterns as any other skill."""
    return scan_skill("skill-builder-v3")


# ── Metric-based scan (v1.2) ─────────────────────────────────────────────────

def _scan_metrics(skill_name: str, root: Path) -> list:
    """Scan metrics/*.jsonl for pattern 12 (dead values), 13 (cumulative regression),
    14 (confidence cliff). Only applies to skill-builder-v3 self-scan.

    These patterns cannot be detected by regex — they require reading metric
    time series and computing statistics.
    """
    findings = []
    metrics_dir = root / "data" / "metrics"
    if not metrics_dir.is_dir():
        return findings

    import json
    from pathlib import Path as _Path

    def _read_jsonl_values(path: str) -> list:
        """Read metric values from a jsonl file. Returns list of dicts with 'value' and 'meta'."""
        vals = []
        fp = metrics_dir / path
        if not fp.exists():
            return vals
        for line in fp.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                # Ensure record has 'value' key; infer from dict if needed
                if "value" in record:
                    vals.append(record)
                elif "meta" not in record:
                    # Legacy format — wrap
                    vals.append({"value": record.get("value", None), "meta": record})
            except json.JSONDecodeError:
                continue
        return vals

    def _last_has_insufficient_data(vals: list, n: int = 5) -> bool:
        """Check if the majority of last N values have insufficient_data flag in meta.
        v1.2: changed from 'all must match' to majority rule — allows transition
        from pre-fix entries that don't have the guard flag.
        """
        if not vals:
            return False
        recent = vals[-min(n, len(vals)):]
        counted = sum(1 for v in recent if v.get("meta", {}).get("insufficient_data", False))
        return counted >= len(recent) * 0.5  # Majority — at least half

    # ── 模式 12: 指标死值 ──
    # 合法稳态：findings=0 在收敛后是合法的
    LEGAL_STEADY_STATES = {
        "total_findings.jsonl": {0},       # 收敛到 0 是合法稳态
        "stubborn_patterns.jsonl": {0},    # 无顽固模式是合法的
        "gepa_trace_failure_rate.jsonl": {0.0},  # 无失败 trace 是合法的
        "generator_noise.jsonl": {0.0},    # 无假阳性噪声是合法稳态
    }

    # 当指标标记 insufficient_data 时，0.0 或恒值不是 bug — 是诚实的状态声明
    INSUFFICIENT_DATA_GUARDED = {
        "ask_gate_confidence_gap.jsonl",   # ask_gate 现在在数据不足时正确返回 0.0
        "verifier_snr.jsonl",              # GVU 现在在无 trace 时标记 unverified
    }

    metric_files = [
        ("verifier_snr.jsonl", 0.05, "verifier_snr", "warning",
         "SNR 连续多轮精确不变——可能为硬编码常量"),
        ("generator_noise.jsonl", 0.01, "generator_noise", "warning",
         "生成器噪声连续多轮不变——可能除零或边界条件锁死"),
        ("false_convergence_rate.jsonl", 0.01, "false_convergence", "warning",
         "假收敛率连续多轮不变——可能为硬编码常量"),
        ("ask_gate_confidence_gap.jsonl", 0.01, "ask_gate_confidence", "warning",
         "置信度间隙连续多轮不变——可能永久卡在悬崖后"),
    ]

    N_DEAD = 5  # 连续 N 次不变判定为死值

    for mfile, epsilon, tag, severity, desc in metric_files:
        vals = _read_jsonl_values(mfile)
        if len(vals) < N_DEAD:
            continue
        recent = vals[-N_DEAD:]
        recent_values = [v["value"] for v in recent]
        unique = set(recent_values)

        if len(unique) == 1:
            steady_val = recent_values[0]
            # Check if this is a legitimate steady state
            legal = LEGAL_STEADY_STATES.get(mfile, set())
            if steady_val in legal:
                continue  # Legitimate steady state, not a dead value
            # Check if insufficient_data guard is active (honest declaration)
            if mfile in INSUFFICIENT_DATA_GUARDED and _last_has_insufficient_data(vals, N_DEAD):
                continue  # Not dead — the system is correctly reporting insufficient data
            # Check if value is in legal tolerance range
            if abs(steady_val) < epsilon and 0 not in legal:
                # Near-zero but not explicitly legal — flag it
                pass
            findings.append({
                "pattern": "metric-dead-value",
                "severity": severity,
                "file": f"data/metrics/{mfile}",
                "line": len(vals),
                "description": f"[模式12] {desc}: 最近 {N_DEAD} 次测量值均为 {steady_val}",
                "match": f"dead_value={steady_val}, samples={N_DEAD}, tag={tag}"
            })

    # ── 模式 14: 置信度悬崖 ──
    confidence_vals = _read_jsonl_values("ask_gate_confidence_gap.jsonl")
    CLIFF_THRESHOLD = 0.5
    CLIFF_RECOVERY_N = 5

    for i in range(1, len(confidence_vals)):
        prev = confidence_vals[i-1]["value"]
        curr = confidence_vals[i]["value"]
        if abs(prev - curr) > CLIFF_THRESHOLD:
            # Check if we're still in cliff N rounds later
            remaining = len(confidence_vals) - i
            if remaining >= CLIFF_RECOVERY_N:
                last_n = [v["value"] for v in confidence_vals[i:]]
                if len(set(last_n)) == 1 and last_n[0] == curr:
                    # Don't flag if post-cliff state is insufficient_data (honest guard)
                    if _last_has_insufficient_data(confidence_vals, remaining):
                        break  # Cliff was a code fix — state is now correctly guarded
                    findings.append({
                        "pattern": "confidence-cliff",
                        "severity": "warning",
                        "file": "data/metrics/ask_gate_confidence_gap.jsonl",
                        "line": i + 1,
                        "description": f"[模式14] 置信度悬崖: {prev}→{curr} 骤降 {abs(prev-curr):.2f}, "
                                       f"此后 {remaining} 轮未恢复",
                        "match": f"cliff from {prev} to {curr}, unresolved for {remaining} rounds"
                    })
            # Only report the first cliff
            break

    # ── 模式 13: 累计值倒退 ──
    loop_state_path = root / "data" / "loop_state.json"
    if loop_state_path.exists():
        try:
            current_state = json.loads(loop_state_path.read_text(encoding="utf-8"))
            # Check cumulative fields against snapshot
            snapshots_dir = root / "data" / "snapshots"
            if snapshots_dir.is_dir():
                snaps = sorted([d for d in snapshots_dir.iterdir() if d.is_dir()])
                if snaps:
                    latest_snap = snaps[-1]
                    snap_state_file = latest_snap / "loop_state.json"
                    if snap_state_file.exists():
                        snap_state = json.loads(snap_state_file.read_text(encoding="utf-8"))
                        curr_details = current_state.get("metrics_snapshot", {}).get("gvu", {}).get("details", {})
                        snap_details = snap_state.get("metrics_snapshot", {}).get("gvu", {}).get("details", {})
                        for field in ["total_fixes", "total_findings"]:
                            curr_val = curr_details.get(field, 0)
                            snap_val = snap_details.get(field, 0)
                            if curr_val < snap_val:
                                findings.append({
                                    "pattern": "cumulative-regression",
                                    "severity": "warning",
                                    "file": "data/loop_state.json",
                                    "line": 0,
                                    "description": f"[模式13] 累计值倒退: {field} {snap_val}→{curr_val} "
                                                   f"(减少 {snap_val - curr_val})",
                                    "match": f"{field}: {snap_val} → {curr_val}"
                                })
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    return findings


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="System 1 快速扫描器——grep/正则模式检测")
    parser.add_argument("skill",
                        help="目标 skill 名称 (默认: skill-builder-v3)")
    parser.add_argument("--severity", choices=["critical", "warning", "info"],
                        help="仅显示指定严重级别")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    target = args.skill
    result = scan_skill(target)

    if "error" in result:
        print(f"[!!] {result['error']}")
        sys.exit(1)

    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"  Scan: {target}")
    print(f"  Findings: {result['findings_count']} "
          f"(严重={result['by_severity']['critical']} "
          f"警告={result['by_severity']['warning']} "
          f"信息={result['by_severity']['info']})")
    print()

    for f in result["findings"]:
        if args.severity and f["severity"] != args.severity:
            continue
        icon = {"critical": "!!!", "warning": "[!]", "info": "[i]"}.get(f["severity"], "?")
        print(f"  {icon} [{f['pattern']}] {f['file']}:{f['line']}")
        print(f"      {f['description']}")
        print(f"      匹配: {f.get('match', '')[:80]}")


if __name__ == "__main__":
    main()
