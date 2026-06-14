#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 2: Deliberator — LLM-powered deep analysis of findings.

When System 1 hits an impasse (unknown pattern, recurring issue, no rule match),
the Deliberator engages. It calls the LLM to:
  1. Understand WHY the finding exists (root cause)
  2. Determine if this is a new pattern or a known one
  3. Generate pattern hypotheses for proposer.py

This is the "System 2" slow path — minutes, not seconds.

v1.1 additions (2026-06-13):
  - CRESCENT consensus deliberation: sample N times, majority vote on pattern novelty
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Prompt construction ───────────────────────────────────────────────────────

def build_deliberation_prompt(skill_name: str, findings: list,
                               iteration_history: list) -> str:
    """Build the System 2 deliberation prompt.

    This prompt is what the LLM sees. It asks for:
    1. Root cause analysis of each finding
    2. Pattern discovery — are these findings related?
    3. Hypothesis generation — what new pattern could prevent these?
    """
    root = _find_skill_root(skill_name)

    # Collect context
    finding_summaries = []
    for f in findings[:20]:
        finding_summaries.append(
            f"- [{f.get('severity', '?')}] {f.get('pattern', 'unknown')}: "
            f"{f.get('file', '?')}:{f.get('line', '?')} — "
            f"{f.get('description', '')[:200]}"
        )

    history_summary = f"过去 {len(iteration_history)} 个事件" if iteration_history else "无历史"

    # Read current patterns
    patterns_content = ""
    if root:
        pp = root / "references" / "patterns.md"
        if pp.exists():
            patterns_content = pp.read_text(encoding="utf-8")[:3000]

    return f"""你是 skill-builder-v3 的 System 2 深度分析器。

目标技能: {skill_name}
历史: {history_summary}

## 当前已知模式
{patterns_content if patterns_content else "(无)"}

## 本轮发现
{chr(10).join(finding_summaries) if finding_summaries else "(无发现)"}

请分析并回答以下问题：

### 1. 根因分析
- 这些发现中有哪些有共同的根因？
- 有没有反复出现但从未被根治的问题？

### 2. 模式发现
- 有没有当前 patterns.md 未覆盖的、值得新增的模式？
- 如果有，请给出：(a) 模式名称 (b) 症状 (c) 受影响的文件类型 (d) 对策 (e) 检查命令

### 3. 防御评估
- 当前 patterns.md 中的检查有哪些可能已经失效？
- 有没有检查存在盲区？

### 4. 架构建议
- 项目结构或流程层面，有没有值得改进的地方？

请用结构化格式回答，便于自动解析。"""


# ── Deliberation runner ───────────────────────────────────────────────────────

def deliberate(skill_name: str, findings: list,
               iteration_history: Optional[list] = None) -> dict:
    """Run System 2 deliberation. Returns structured analysis.

    NOTE: This function builds the prompt. The actual LLM call is made
    by the caller (Claude Code or a script that invokes the API).
    This module provides the prompt construction and result parsing.

    For automated runs, use the --prompt-only flag to output the prompt,
    then pipe it to the LLM.
    """
    if iteration_history is None:
        from engine import memory as mem
        iteration_history = memory.read_events(skill_name, limit=30)

    prompt = build_deliberation_prompt(skill_name, findings, iteration_history)

    return {
        "skill": skill_name,
        "prompt": prompt,
        "findings_count": len(findings),
        "history_events": len(iteration_history),
        "iso_generated": datetime.now(timezone.utc).isoformat(),
        "status": "prompt_ready"  # caller feeds this to LLM
    }


def parse_deliberation_response(response_text: str) -> dict:
    """Parse the LLM's deliberation response into structured data."""
    return {
        "raw": response_text,
        "root_causes": _extract_section(response_text, "根因分析"),
        "new_patterns": _extract_section(response_text, "模式发现"),
        "defense_gaps": _extract_section(response_text, "防御评估"),
        "architecture_suggestions": _extract_section(response_text, "架构建议"),
    }


def _extract_section(text: str, section_name: str) -> str:
    """Extract a named section from the deliberation response."""
    lines = text.split("\n")
    in_section = False
    result = []
    for line in lines:
        if section_name in line and line.strip().startswith("#"):
            in_section = True
            continue
        if in_section and line.strip().startswith("#") and "##" in line:
            break
        if in_section:
            result.append(line)
    return "\n".join(result).strip()


# ── CRESCENT Consensus Deliberation ───────────────────────────────────────────

def build_consensus_prompts(skill_name: str, findings: list,
                             n: int = 3,
                             iteration_history: Optional[list] = None) -> list:
    """(CRESCENT) Build N deliberation prompts with varied framing.

    Each prompt asks the same analytical questions but from a different angle.
    After the caller feeds all N to the LLM, resolve_consensus() picks the
    majority judgment on whether findings represent new patterns.
    """
    if iteration_history is None:
        from engine import memory as mem
        iteration_history = memory.read_events(skill_name, limit=30)

    prompts = []
    framings = [
        "你是一个系统架构师。从架构层面分析这些发现。",
        "你是一个模式识别专家。从模式层面分析这些发现是否有共同根因。",
        "你是一个安全审计员。从防御有效性层面分析这些发现。"
    ]

    for i in range(min(n, len(framings))):
        base = build_deliberation_prompt(skill_name, findings, iteration_history)
        lines = base.split("\n")
        lines[0] = framings[i]
        lines.insert(1, f"\n(分析视角 #{i+1})")
        prompts.append("\n".join(lines))

    return prompts


def resolve_consensus(responses: list) -> dict:
    """(CRESCENT) Resolve multiple deliberation responses by majority vote.

    Key judgments:
    1. Is there a common root cause? (majority vote)
    2. Should a new pattern be created? (majority vote)
    3. What defense gaps exist? (union of all responses)
    """
    if not responses:
        return {"verdict": "inconclusive", "reason": "无响应"}

    parsed = [parse_deliberation_response(r) for r in responses]

    # Check for consensus on new patterns
    new_pattern_texts = [p.get("new_patterns", "") for p in parsed]
    has_new_pattern = sum(1 for t in new_pattern_texts if len(t.strip()) > 50)

    # Check for consensus on root causes
    root_cause_texts = [p.get("root_causes", "") for p in parsed]
    has_root_cause = sum(1 for t in root_cause_texts if len(t.strip()) > 50)

    # Collect all defense gaps (union, not intersection)
    all_defense_gaps = []
    for p in parsed:
        gap = p.get("defense_gaps", "")
        if gap.strip():
            all_defense_gaps.append(gap)

    # Collect all architecture suggestions (union)
    all_arch = []
    for p in parsed:
        arch = p.get("architecture_suggestions", "")
        if arch.strip():
            all_arch.append(arch)

    return {
        "verdict": "new_pattern_discovered" if has_new_pattern >= 2 else "no_new_pattern",
        "method": "CRESCENT_consensus",
        "samples": len(responses),
        "new_pattern_consensus": has_new_pattern,
        "root_cause_consensus": has_root_cause,
        "all_defense_gaps": all_defense_gaps,
        "all_architecture_suggestions": all_arch,
        "consensus_strength": max(has_new_pattern, len(responses) - has_new_pattern) / len(responses)
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="System 2 深度分析器——LLM 驱动的根因分析")
    parser.add_argument("skill",
                        help="目标 skill 名称")
    parser.add_argument("--findings-file", help="从 JSON 文件读取 findings")
    parser.add_argument("--prompt-only", action="store_true",
                        help="仅输出 prompt（不调用 LLM）")
    parser.add_argument("--parse", help="解析 LLM 响应文本文件")

    args = parser.parse_args()

    if args.parse:
        fp = Path(args.parse)
        if fp.exists():
            result = parse_deliberation_response(fp.read_text(encoding="utf-8"))
            print(f"  根因分析: {len(result.get('root_causes', ''))} chars")
            print(f"  新模式:   {len(result.get('new_patterns', ''))} chars")
            print(f"  防御缺口: {len(result.get('defense_gaps', ''))} chars")
            print(f"  架构建议: {len(result.get('architecture_suggestions', ''))} chars")
        else:
            print(f"[!!] File not found: {args.parse}")
            sys.exit(1)
        return

    # Load findings
    findings = []
    if args.findings_file:
        fp = Path(args.findings_file)
        if fp.exists():
            findings = json.loads(fp.read_text(encoding="utf-8")).get("findings", [])

    if not findings:
        from engine.system1 import scanner as scan
        result = scan.scan_skill(args.skill)
        findings = result.get("findings", [])

    if not findings:
        print("[OK] 无发现——System 2 无需介入")
        return

    result = deliberate(args.skill, findings)

    if args.prompt_only:
        print(result["prompt"])
    else:
        print(f"  System 2 分析准备就绪")
        print(f"  发现数: {result['findings_count']}")
        print(f"  历史事件: {result['history_events']}")
        print(f"  Prompt 长度: {len(result['prompt'])} chars")
        print(f"\n  --prompt-only 查看完整 prompt")


if __name__ == "__main__":
    main()
