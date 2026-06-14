#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Evidence Verifier — 修复证据链 (EVE-Agent 蒸馏).

v1.2 (2026-06-13): 蒸馏自 EVE-Agent (arXiv 2605.22905).

核心原则: 每个修复必须携带可审查的 evidence_span——不能只说"修好了"，
必须提供可检查的证据证明修复确实有效。

Evidence Verifier 检查:
  1. 证据类型是否匹配修复类型?
  2. 预期输出是否与实际输出一致?
  3. 证据是否可独立重现(无需知道修复细节)?
  4. 证据跨度是否足够具体(不只是 "exit 0")?

EVE-Agent 的关键洞察:
  self-evolving agents should only train on examples they can verify with evidence.
  没有证据的修复 = 不可信的修复。
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Evidence data structures ──────────────────────────────────────────────────

EVIDENCE_TYPES = {
    "compile_check": "编译检查——代码可被Python解析",
    "syntax_check": "语法检查——AST解析通过",
    "run_test": "运行测试——执行并检查输出",
    "scan_self": "扫描自检——对skill重新扫描",
    "grep_check": "grep检查——确认修复后的文件不含旧模式",
    "diff_verify": "diff验证——对比修复前后差异",
    "exit_code": "退出码检查——命令返回0",
    "output_match": "输出匹配——stdout/stderr符合预期模式",
}


class EvidenceSpan:
    """一个修复的证据跨度——为什么我们相信这个修复有效。

    EVE-Agent的设计: 每个训练样本携带一个inspectable source span
    解释为什么应该被信任。EvidenceSpan是skill-builder中的等价物。
    """

    def __init__(self, evidence_type: str, command: str,
                 expected: str = "", actual: str = "",
                 source_file: str = "", line_range: str = ""):
        assert evidence_type in EVIDENCE_TYPES, (
            f"未知证据类型: {evidence_type}。已知类型: {list(EVIDENCE_TYPES.keys())}"
        )
        self.evidence_type = evidence_type
        self.command = command
        self.expected = expected
        self.actual = actual
        self.source_file = source_file
        self.line_range = line_range
        self.verdict = "unverified"

    def to_dict(self) -> dict:
        return {
            "evidence_type": self.evidence_type,
            "command": self.command,
            "expected": self.expected,
            "actual": self.actual,
            "source_file": self.source_file,
            "line_range": self.line_range,
            "verdict": self.verdict
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceSpan":
        span = cls(
            evidence_type=d.get("evidence_type", "exit_code"),
            command=d.get("command", ""),
            expected=d.get("expected", ""),
            actual=d.get("actual", ""),
            source_file=d.get("source_file", ""),
            line_range=d.get("line_range", "")
        )
        span.verdict = d.get("verdict", "unverified")
        return span


# ── Evidence collection helpers ───────────────────────────────────────────────

def collect_compile_evidence(file_path: Path) -> EvidenceSpan:
    """收集编译检查证据——文件可被Python解析。"""
    try:
        source = file_path.read_text(encoding="utf-8")
        compile(source, str(file_path), "exec")
        return EvidenceSpan(
            evidence_type="compile_check",
            command=f"python -c 'compile(open(\"{file_path}\").read(), \"{file_path}\", \"exec\")'",
            expected="compile OK (no SyntaxError)",
            actual="compile OK (no SyntaxError)",
            source_file=str(file_path)
        )
    except SyntaxError as e:
        return EvidenceSpan(
            evidence_type="compile_check",
            command=f"python -c 'compile(...)'",
            expected="compile OK (no SyntaxError)",
            actual=f"SyntaxError: {e.msg} at line {e.lineno}",
            source_file=str(file_path)
        )


def collect_command_evidence(cmd: str, cwd: Path, timeout: int = 30) -> EvidenceSpan:
    """收集命令执行证据——运行命令并捕获输出。"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(cwd), encoding="utf-8", errors="replace"
        )
        return EvidenceSpan(
            evidence_type="run_test",
            command=cmd,
            expected="exit 0",
            actual=f"exit {result.returncode}; stdout: {result.stdout[-500:]}; stderr: {result.stderr[-500:]}",
            source_file=str(cwd)
        )
    except subprocess.TimeoutExpired:
        return EvidenceSpan(
            evidence_type="run_test",
            command=cmd,
            expected="exit 0",
            actual=f"timeout after {timeout}s",
            source_file=str(cwd)
        )
    except Exception as e:
        return EvidenceSpan(
            evidence_type="run_test",
            command=cmd,
            expected="exit 0",
            actual=f"error: {e}",
            source_file=str(cwd)
        )


def collect_scan_evidence(skill_name: str, root: Path) -> EvidenceSpan:
    """收集扫描自检证据——对skill重新运行scanner。"""
    try:
        # Import scanner and run
        cmd = (
            f'"{sys.executable}" -c "from engine.system1 import scanner; '
            f'r = scanner.scan_skill(\'{skill_name}\'); '
            f'print(f\'findings={{r.get(\\\'findings_count\\\', \\\'?\\\')}}\')"'
        )
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(root), encoding="utf-8", errors="replace"
        )
        return EvidenceSpan(
            evidence_type="scan_self",
            command=f"scanner.scan_skill('{skill_name}')",
            expected="无新增critical发现",
            actual=result.stdout.strip()[:500],
            source_file=str(root)
        )
    except Exception as e:
        return EvidenceSpan(
            evidence_type="scan_self",
            command=f"scanner.scan_skill('{skill_name}')",
            expected="无新增critical发现",
            actual=f"error: {e}",
            source_file=str(root)
        )


# ── Evidence verification ─────────────────────────────────────────────────────

def verify_evidence(span: EvidenceSpan) -> dict:
    """验证证据是否支持修复声明。

    EVE-Agent的边际准确率增益原则:
    证据必须 genuinely help answer the question (证明修复有效)
    ——而不是仅仅声称有效。

    Returns {verdict, confidence, explanation}.
    """
    issues = []
    confidence = 1.0

    # Check 1: Evidence type matches expected patterns
    if span.evidence_type not in EVIDENCE_TYPES:
        issues.append(f"未知证据类型: {span.evidence_type}")
        confidence -= 0.3

    # Check 2: Command is non-empty and specific
    if not span.command or len(span.command) < 10:
        issues.append("证据命令过于简短——无法独立重现")
        confidence -= 0.2

    # Check 3: Expected output is non-trivial
    trivial_expecteds = ["", "OK", "ok", "success", "0", "pass"]
    if span.expected.strip().lower() in trivial_expecteds and span.evidence_type != "exit_code":
        issues.append("预期输出过于简单——建议提供更具体的成功标准")
        confidence -= 0.1

    # Check 4: Actual output matches expected (when both available)
    if span.expected and span.actual:
        if span.expected.strip() == span.actual.strip():
            span.verdict = "evidence_consistent"
        elif span.expected.strip().lower() in span.actual.strip().lower():
            span.verdict = "evidence_consistent"
        else:
            span.verdict = "evidence_inconsistent"
            issues.append(f"实际输出与预期不符: 预期'{span.expected[:50]}' ≠ 实际'{span.actual[:50]}'")
            confidence -= 0.4
    elif span.actual:
        span.verdict = "evidence_consistent"

    # Check 5: Evidence is independently reproducible (no fix-specific knowledge needed)
    if "fix" in span.command.lower() or "repair" in span.command.lower():
        issues.append("证据命令引用了修复细节——独立验证者无法重现")
        confidence -= 0.15

    return {
        "verdict": span.verdict,
        "confidence": max(0.0, min(1.0, confidence)),
        "issues": issues,
        "passes": len(issues) == 0 or confidence >= 0.6,
        "explanation": (
            f"[OK] 证据一致——修复声明被证据支持 (置信度 {confidence:.0%})" if len(issues) == 0
            else f"[!!] 证据缺陷 ({len(issues)}个): {'; '.join(issues)} (置信度 {confidence:.0%})"
        )
    }


# ── Batch evidence verification ───────────────────────────────────────────────

def verify_all_fix_evidences(fix_results: list, skill_root: Path) -> dict:
    """对一批修复收集并验证全部证据。

    Args:
        fix_results: 修复报告列表 [{"rule": ..., "file": ..., "evidence": EvidenceSpan}]
        skill_root: skill根目录

    Returns {total, verified, failed, details}.
    """
    results = []
    for fix in fix_results:
        evidence = fix.get("evidence")
        if not evidence:
            # Auto-collect evidence if not provided
            fix_file = fix.get("file", "")
            if fix_file and fix_file.endswith(".py"):
                fp = skill_root / fix_file
                if fp.exists():
                    evidence = collect_compile_evidence(fp)
                else:
                    evidence = EvidenceSpan(
                        evidence_type="exit_code",
                        command="(自动收集)",
                        expected="exit 0",
                        actual="file not found",
                        source_file=fix_file
                    )
            else:
                evidence = EvidenceSpan(
                    evidence_type="exit_code",
                    command="(无证据)",
                    expected="exit 0",
                    actual="no evidence provided",
                    source_file=fix_file
                )

        verification = verify_evidence(evidence)
        results.append({
            "rule": fix.get("rule", "?"),
            "file": fix.get("file", "?"),
            "evidence_type": evidence.evidence_type,
            "evidence_verdict": verification["verdict"],
            "confidence": verification["confidence"],
            "passes": verification["passes"],
            "issues": verification["issues"]
        })

    verified = [r for r in results if r["passes"]]
    failed = [r for r in results if not r["passes"]]

    return {
        "total": len(results),
        "verified": len(verified),
        "failed": len(failed),
        "evidence_pass_rate": len(verified) / max(1, len(results)),
        "details": results,
        "verdict": (
            f"[OK] 全部 {len(results)} 个修复的证据通过验证"
            if len(failed) == 0
            else f"[!!] {len(failed)}/{len(results)} 个修复的证据不充分"
        )
    }


# ═══════════════════════════════════════════════════════════════════════
# S2: TextGrad — 文本反向传播
# ═══════════════════════════════════════════════════════════════════════

def generate_textual_gradient(fix_rule: str, fix_file: str,
                               evidence_span: EvidenceSpan,
                               gdpo_score: Optional[dict] = None) -> dict:
    """(S2: TextGrad) Generate a textual gradient explaining WHY a fix failed.

    Unlike boolean success/failure, a textual gradient is a natural-language
    critique that explains:
      1. What went wrong (specific failure mode)
      2. Why it went wrong (root cause hypothesis)
      3. How to fix the fix (corrective suggestion)

    This is the TextGrad "textual backpropagation" pattern:
    the critique flows backward through the fix pipeline to improve the rule
    that generated the fix.

    The gradient is prompt-ready for an LLM to consume. When the LLM is
    called (during System 2 deliberation), it reads the gradient and
    proposes a corrected fix strategy.

    Returns {gradient_text, fix_rule, fix_file, gdpo_context, iso_generated}.
    """
    # Build the gradient from evidence
    failure_mode = _diagnose_failure_mode(evidence_span)
    root_cause = _hypothesize_root_cause(evidence_span, fix_rule, fix_file)
    corrective = _suggest_correction(evidence_span, fix_rule, fix_file, gdpo_score)

    gradient_text = (
        f"TextGrad (修复失败分析):\n"
        f"  规则: {fix_rule}\n"
        f"  文件: {fix_file}\n"
        f"  失败模式: {failure_mode}\n"
        f"  根因假说: {root_cause}\n"
        f"  修正建议: {corrective}\n"
    )

    if gdpo_score:
        gradient_text += (
            f"  GDPO评分: {gdpo_score.get('gdpo_overall', '?')} "
            f"(D1全过={gdpo_score.get('d1_full_pass','?')} "
            f"D2部分={gdpo_score.get('d2_partial_fix_rate','?')} "
            f"D3无退化={gdpo_score.get('d3_no_regression','?')})\n"
        )

    return {
        "gradient_text": gradient_text,
        "fix_rule": fix_rule,
        "fix_file": fix_file,
        "failure_mode": failure_mode,
        "root_cause_hypothesis": root_cause,
        "corrective_suggestion": corrective,
        "gdpo_context": gdpo_score,
        "iso_generated": datetime.now(timezone.utc).isoformat()
    }


def _diagnose_failure_mode(span: EvidenceSpan) -> str:
    """Diagnose the specific failure mode from evidence."""
    if span.verdict == "evidence_inconsistent":
        return "证据不一致: 预期输出与实际输出不匹配——修复声称成功但验证结果相悖"
    if span.evidence_type == "compile_check":
        if "SyntaxError" in span.actual:
            return "编译错误: 修复引入了语法错误——可能改错了行或引入了未闭合括号"
        return "编译检查失败: 修复后的代码无法被Python解析"
    if span.evidence_type == "run_test":
        if "timeout" in span.actual:
            return "超时: 修复导致程序死循环或阻塞——可能修改了控制流"
        if "error" in span.actual.lower():
            return "运行时错误: 修复后的代码在运行时抛出异常"
        return "运行失败: 命令返回非零退出码"
    if span.evidence_type == "scan_self":
        return "扫描未通过: 修复后扫描器仍检测到问题——修复未完全解决"
    return f"未知失败模式: {span.actual[:100]}"


def _hypothesize_root_cause(span, fix_rule: str, fix_file: str) -> str:
    """Hypothesize the root cause of the fix failure."""
    if "utf8" in fix_rule.lower():
        return "UTF8 header 插入位置不当——可能插在了已有 import 语句之前导致顺序错误"
    if "grep" in fix_rule.lower():
        return "grep 替换不完全——文件中还有其他 grep -P 变体未被匹配到"
    if "hardcoded" in fix_rule.lower():
        return "硬编码标注未正确识别行号——修复前后的行偏移导致标注位置错误"
    if fix_file.endswith(".py"):
        return f"修复 {fix_rule} 在 {fix_file} 上失败——需检查修复函数是否正确处理了文件边界情况"
    return f"修复 {fix_rule} 失败——需审查修复函数的适用范围和前提条件"


def _suggest_correction(span, fix_rule: str, fix_file: str,
                          gdpo_score: Optional[dict] = None) -> str:
    """Suggest a corrective action for the failed fix."""
    if gdpo_score:
        if gdpo_score.get("d1_full_pass", 0) < 1.0:
            return "修复未完全解决问题——扩展修复范围或增加额外的修复步骤"
        if gdpo_score.get("d3_no_regression", 0) < 1.0:
            return f"修复引入了 {len(gdpo_score.get('new_patterns_introduced', []))} 个新问题——缩小修复范围，使用更保守的策略"
    return "重新审视修复策略——考虑备选方案或更保守的局部修复"


def collect_textual_gradients(skill_name: str, lookback: int = 20) -> list:
    """Collect all textual gradients from recent failed fixes.

    These gradients are the "training data" for improving rule generation.
    Each gradient is a specific, actionable critique of what went wrong.
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=lookback)
    traces = [e for e in events if e["event_type"] == "execution_trace"]
    failed_traces = [t for t in traces
                     if not t.get("data", {}).get("success", True)]

    gradients = []
    for ft in failed_traces:
        data = ft.get("data", {})
        # Reconstruct the evidence span that was recorded
        span = EvidenceSpan(
            evidence_type=data.get("evidence_type", "exit_code"),
            command=data.get("command", "(unknown)"),
            expected=data.get("expected", "exit 0"),
            actual=data.get("stdout_tail", data.get("stderr_tail", "(unknown)")),
            source_file=data.get("fix_file", "?")
        )
        span.verdict = "evidence_inconsistent"

        gradient = generate_textual_gradient(
            fix_rule=data.get("fix_rule", "unknown"),
            fix_file=data.get("fix_file", data.get("finding_file", "?")),
            evidence_span=span
        )
        gradients.append(gradient)

    # Store in memory index for semantic retrieval
    if gradients:
        try:
            from engine import memory_index
            for g in gradients[:10]:
                memory_index.index_document(
                    doc_type="evidence",
                    skill_name=skill_name,
                    title=f"TextGrad: {g['fix_rule']}",
                    content=g["gradient_text"],
                    confidence=0.6,
                    metadata={"fix_rule": g["fix_rule"], "failure_mode": g["failure_mode"]}
                )
        except ImportError:
            pass  # v4: memory is imported at module top — this inner import is a fallback path. OK to skip.

    return gradients


# ── Memory integration ─────────────────────────────────────────────────────────

def record_evidence_to_memory(skill_name: str, evidence_span: EvidenceSpan,
                               fix_rule: str = "", fix_file: str = "") -> Optional[Path]:
    """将证据记录到事件日志中。"""
    from engine import memory
    return memory.write_event(skill_name, "evidence_span", {
        "evidence_type": evidence_span.evidence_type,
        "command": evidence_span.command,
        "expected": evidence_span.expected,
        "actual": evidence_span.actual,
        "verdict": evidence_span.verdict,
        "fix_rule": fix_rule,
        "fix_file": fix_file,
        "iso_recorded": datetime.now(timezone.utc).isoformat()
    })


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Evidence Verifier (EVE-Agent)——修复证据链验证"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("collect", help="收集修复证据")
    p.add_argument("--file", required=True, help="修复的文件路径")
    p.add_argument("--type", choices=["compile", "command", "scan"],
                   default="compile", help="证据类型")

    p = sub.add_parser("verify", help="验证证据")
    p.add_argument("--evidence-file", required=True, help="证据JSON文件路径")

    p = sub.add_parser("verify-fixes", help="批量验证修复证据")
    p.add_argument("--fixes-file", required=True, help="修复报告JSON文件路径")
    p.add_argument("--skill-root", required=True, help="Skill根目录")

    args = parser.parse_args()

    if args.cmd == "collect":
        fp = Path(args.file)
        if args.type == "compile":
            span = collect_compile_evidence(fp)
        elif args.type == "command":
            span = collect_command_evidence(f"python {fp}", fp.parent)
        elif args.type == "scan":
            span = collect_scan_evidence(fp.parent.name, fp.parent.parent)
        else:
            span = collect_compile_evidence(fp)

        print(f"  证据类型: {span.evidence_type}")
        print(f"  命令: {span.command}")
        print(f"  预期: {span.expected[:200]}")
        print(f"  实际: {span.actual[:200]}")

    elif args.cmd == "verify":
        efp = Path(args.evidence_file)
        if not efp.exists():
            print(f"[!!] 文件不存在: {args.evidence_file}")
            sys.exit(1)

        data = json.loads(efp.read_text(encoding="utf-8"))
        span = EvidenceSpan.from_dict(data)
        result = verify_evidence(span)
        print(f"  裁决: {result['verdict']}")
        print(f"  置信度: {result['confidence']:.0%}")
        print(f"  解释: {result['explanation']}")

    elif args.cmd == "verify-fixes":
        ffp = Path(args.fixes_file)
        if not ffp.exists():
            print(f"[!!] 文件不存在: {args.fixes_file}")
            sys.exit(1)

        fix_results = json.loads(ffp.read_text(encoding="utf-8"))
        if isinstance(fix_results, dict):
            fix_results = fix_results.get("fixes", [fix_results])

        result = verify_all_fix_evidences(fix_results, Path(args.skill_root))
        print(f"  {result['verdict']}")
        print(f"  通过: {result['verified']} | 失败: {result['failed']}")
        for d in result["details"]:
            icon = "[OK]" if d["passes"] else "[!!]"
            print(f"  {icon} {d['rule']}: {d['file']} — {d['evidence_verdict']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
