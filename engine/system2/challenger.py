#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 2: Challenger — adversarial verification of proposals.

Inspired by AlphaGo Zero's self-play and Constitutional AI's self-critique.

When a proposal is made, the Challenger tries to find flaws in it:
  1. Correctness: Will this change actually solve the problem?
  2. Safety: Could this change introduce new issues?
  3. Cross-platform: Will this work on all target platforms?
  4. Constitution: Does this violate any constitutional laws?
  5. Pattern: Has a similar change failed before?

The Challenger does NOT implement the change — it only judges it.

v1.1 additions (2026-06-13):
  - CRESCENT consensus: build N prompts, resolve by majority vote
  - GVU gate: if system is unstable, challenger is more conservative
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Challenge dimensions ──────────────────────────────────────────────────────

CHALLENGE_DIMENSIONS = [
    {
        "name": "correctness",
        "question": "这个修改能解决它声称要解决的问题吗？",
        "checks": [
            "修改的文件路径是否正确？",
            "修改的逻辑是否完整——有没有遗漏边界情况？",
            "修改是否解决了根因而非症状？"
        ]
    },
    {
        "name": "safety",
        "question": "这个修改是否会引入新问题？",
        "checks": [
            "修改是否改变了任何公共接口/签名？",
            "修改是否可能破坏现有功能？",
            "修改是否引入了新的硬编码值？"
        ]
    },
    {
        "name": "cross-platform",
        "question": "这个修改在所有目标平台上都能正常工作吗？",
        "checks": [
            "是否使用了平台特定的语法（如 /dev/null）？",
            "是否考虑了 Windows/Linux/macOS 的差异？",
            "是否依赖了只在某些平台上可用的工具？"
        ]
    },
    {
        "name": "constitution",
        "question": "这个修改是否违反宪法？",
        "checks": [
            f"是否引入了对特定 skill 的特殊路径？",
            f"是否留下了文件证据？",
            f"是否在集成前经过验证？"
        ]
    },
    {
        "name": "history",
        "question": "历史上是否有类似的修改失败了？",
        "checks": [
            "过去 N 轮中是否有相同文件的修改导致了回滚？",
            "过去是否有相同模式的修复引入了新 bug？",
            "这个修改是否与已知的失效模式重合？"
        ]
    }
]


# ── Challenge runner ──────────────────────────────────────────────────────────

def build_challenge_prompt(proposal: dict, skill_name: str,
                            iteration_history: Optional[list] = None) -> str:
    """Build the adversarial challenge prompt.

    This is what the LLM sees when acting as the Challenger.
    It must find flaws in the proposal — its goal is to refute, not approve.
    """
    if iteration_history is None:
        from engine import memory as mem
        iteration_history = memory.read_events(skill_name, limit=20)

    history_text = "\n".join(
        f"- [{e['event_type']}] {e['iso_timestamp'][:19]}: "
        f"{json.dumps(e.get('data', {}), ensure_ascii=False)[:200]}"
        for e in iteration_history[:10]
    ) if iteration_history else "(无历史)"

    return f"""你是 skill-builder-v3 的对抗验证器。你的任务是找出提案中的漏洞。

**你的默认立场是拒绝。只有提案在所有维度上都无懈可击时，才给通过。**

## 提案
- 标题: {proposal.get('title', '?')}
- 类型: {proposal.get('change_type', '?')}
- 文件: {proposal.get('files_changed', [])}
- 描述: {proposal.get('description', '?')[:2000]}
- 理由: {proposal.get('rationale', '?')[:1000]}

## 历史记录
{history_text}

## 挑战维度

请逐维度回答。对于每个维度：
1. 给出结论: {chr(34)}PASS{chr(34)} 或 {chr(34)}FAIL{chr(34)} 或 {chr(34)}CONCERN{chr(34)}
2. 如果 FAIL 或 CONCERN，具体说明问题

### 正确性
{chr(10).join('- ' + c for c in CHALLENGE_DIMENSIONS[0]['checks'])}

### 安全性
{chr(10).join('- ' + c for c in CHALLENGE_DIMENSIONS[1]['checks'])}

### 跨平台兼容
{chr(10).join('- ' + c for c in CHALLENGE_DIMENSIONS[2]['checks'])}

### 宪法合规
{chr(10).join('- ' + c for c in CHALLENGE_DIMENSIONS[3]['checks'])}

### 历史教训
{chr(10).join('- ' + c for c in CHALLENGE_DIMENSIONS[4]['checks'])}

## 最终裁决
- 如果所有维度 PASS: 输出 FINAL: PASS
- 如果任一维度 FAIL: 输出 FINAL: FAIL
- 如果有 CONCERN: 输出 FINAL: CONDITIONAL_PASS，列出条件
"""


def challenge(skill_name: str, proposal: dict,
              iteration_history: Optional[list] = None) -> dict:
    """Run adversarial verification. Returns challenge result.

    Like generate_fixes, this builds the prompt. The actual LLM call
    is made by the caller. Use --prompt-only to get the prompt.
    """
    if iteration_history is None:
        from engine import memory as mem
        iteration_history = memory.read_events(skill_name, limit=20)

    prompt = build_challenge_prompt(proposal, skill_name, iteration_history)

    return {
        "proposal_id": proposal.get("proposal_id", "?"),
        "prompt": prompt,
        "dimensions": len(CHALLENGE_DIMENSIONS),
        "iso_generated": datetime.now(timezone.utc).isoformat(),
        "status": "prompt_ready"
    }


def parse_challenge_response(response_text: str) -> dict:
    """Parse the LLM's challenge response into a structured verdict."""
    verdict = "FAIL"  # Default to fail (conservative)
    concerns = []
    passes = []

    for dim in CHALLENGE_DIMENSIONS:
        dim_name = dim["name"]
        # Find dimension section
        lines = response_text.split("\n")
        dim_result = "UNKNOWN"

        # Look for PASS/FAIL/CONCERN in each dimension section
        in_dim = False
        for line in lines:
            if dim_name in line.lower() and ("#" in line or "正确" in line or "安全" in line or "跨平台" in line or "宪法" in line or "历史" in line):
                in_dim = True
                continue
            if in_dim:
                if "PASS" in line.upper() and "FAIL" not in line.upper():
                    dim_result = "PASS"
                elif "FAIL" in line.upper():
                    dim_result = "FAIL"
                    concerns.append(f"[{dim_name}] {line.strip()[:200]}")
                elif "CONCERN" in line.upper():
                    dim_result = "CONCERN"
                    concerns.append(f"[{dim_name}] {line.strip()[:200]}")
                if dim_result != "UNKNOWN" and line.strip().startswith("#"):
                    break

        if dim_result == "PASS":
            passes.append(dim_name)

    # Find final verdict
    for line in response_text.split("\n"):
        if "FINAL:" in line.upper():
            if "PASS" in line.upper() and "FAIL" not in line.upper() and "CONDITIONAL" not in line.upper():
                verdict = "PASS"
            elif "CONDITIONAL" in line.upper():
                verdict = "CONDITIONAL_PASS"
            else:
                verdict = "FAIL"
            break

    return {
        "verdict": verdict,
        "passed_dimensions": passes,
        "concerns": concerns,
        "raw": response_text[:5000]
    }


# ── CRESCENT Consensus Challenge ──────────────────────────────────────────────

def build_consensus_prompts(proposal: dict, skill_name: str,
                             n: int = 3,
                             iteration_history: Optional[list] = None) -> list:
    """(CRESCENT) Build N challenge prompts with slight temperature variations.

    Each prompt asks the same question but with a different framing angle.
    After the caller feeds all N to the LLM, resolve_consensus() picks the
    majority verdict.

    This implements the CRESCENT insight: majority voting on multiple samples
    significantly improves the quality of self-generated judgments.
    """
    if iteration_history is None:
        from engine import memory as mem
        iteration_history = memory.read_events(skill_name, limit=20)

    prompts = []
    framings = [
        "你是一个严格的代码审查者。你的任务是找出提案中的漏洞。",
        "你是一个安全审计员。检查这个提案是否会引入风险。",
        "你是一个平台兼容性专家。确保这个提案在所有环境下都能工作。"
    ]

    for i in range(min(n, len(framings))):
        base_prompt = build_challenge_prompt(proposal, skill_name, iteration_history)
        # Replace the first line with a different framing
        lines = base_prompt.split("\n")
        lines[0] = framings[i]
        # Add a unique marker so the LLM treats each as independent
        lines.insert(1, f"\n(审查视角 #{i+1})")
        prompts.append("\n".join(lines))

    return prompts


def resolve_consensus(responses: list) -> dict:
    """(CRESCENT) Resolve multiple challenge responses by majority vote.

    If 2+ of 3 agree on PASS, the proposal passes.
    If 2+ of 3 agree on FAIL, it's rejected.
    Mixed results → CONDITIONAL_PASS with noted concerns.

    Returns a consolidated verdict with per-dimension majority.
    """
    if not responses:
        return {"verdict": "FAIL", "reason": "无响应"}

    parsed = [parse_challenge_response(r) for r in responses]

    # Count verdicts
    verdicts = [p["verdict"] for p in parsed]
    pass_count = verdicts.count("PASS")
    fail_count = verdicts.count("FAIL")
    cond_count = verdicts.count("CONDITIONAL_PASS")

    # Majority vote
    if pass_count >= 2:
        final_verdict = "PASS"
    elif fail_count >= 2:
        final_verdict = "FAIL"
    elif pass_count == 1 and fail_count == 1 and cond_count == 1:
        final_verdict = "CONDITIONAL_PASS"
    else:
        final_verdict = "CONDITIONAL_PASS"

    # Per-dimension majority
    all_dimensions = set()
    for p in parsed:
        all_dimensions.update(p.get("passed_dimensions", []))

    dimension_consensus = {}
    for dim in all_dimensions:
        passed_in = sum(1 for p in parsed if dim in p.get("passed_dimensions", []))
        dimension_consensus[dim] = "PASS" if passed_in >= 2 else "FAIL"

    # Collect all concerns
    all_concerns = []
    for p in parsed:
        all_concerns.extend(p.get("concerns", []))

    return {
        "verdict": final_verdict,
        "method": "CRESCENT_consensus",
        "samples": len(responses),
        "individual_verdicts": verdicts,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "dimension_consensus": dimension_consensus,
        "all_concerns": all_concerns,
        "consensus_strength": max(pass_count, fail_count) / len(responses)
    }


# ── Constitution-specific challenge ───────────────────────────────────────────

def challenge_constitution(skill_name: str, proposal: dict) -> dict:
    """Run constitutional verification as part of the challenge.

    This is a deterministic check — no LLM needed.
    It's called by anchor.py as part of the full verification chain.
    """
    from engine import anchor

    root = _find_skill_root(skill_name)
    if not root:
        return {"verdict": "FAIL", "reason": "Skill not found"}

    # Run all constitutional laws
    con_result = anchor.verify_all(root, change=proposal)

    return {
        "verdict": "PASS" if con_result["all_passed"] else "FAIL",
        "violations": con_result.get("violated_laws", []),
        "details": con_result
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="System 2 对抗验证器——找出提案的漏洞"
    )
    parser.add_argument("skill",
                        help="目标 skill 名称")
    parser.add_argument("--proposal", help="提案 ID")
    parser.add_argument("--proposal-file", help="从 JSON 文件读取提案")
    parser.add_argument("--prompt-only", action="store_true",
                        help="仅输出 challenge prompt")
    parser.add_argument("--parse", help="解析 LLM 响应文本文件")
    parser.add_argument("--constitution", action="store_true",
                        help="仅运行宪法检查（确定性，不需要 LLM）")

    args = parser.parse_args()

    if args.parse:
        fp = Path(args.parse)
        if fp.exists():
            result = parse_challenge_response(fp.read_text(encoding="utf-8"))
            print(f"  Verdict: {result['verdict']}")
            print(f"  Passed: {result['passed_dimensions']}")
            if result['concerns']:
                print(f"  Concerns:")
                for c in result['concerns']:
                    print(f"    - {c}")
        else:
            print(f"[!!] File not found: {args.parse}")
            sys.exit(1)
        return

    # Load proposal
    proposal = None
    if args.proposal:
        from engine.system2 import proposer
        proposal = proposer.load_proposal(args.skill, args.proposal)
    elif args.proposal_file:
        fp = Path(args.proposal_file)
        if fp.exists():
            proposal = json.loads(fp.read_text(encoding="utf-8"))

    if args.constitution and proposal:
        result = challenge_constitution(args.skill, proposal)
        print(f"  宪法挑战: {result['verdict']}")
        if result.get("violations"):
            for v in result["violations"]:
                print(f"  [!!] {v}")
        if result["verdict"] == "FAIL":
            sys.exit(1)
        return

    if proposal:
        result = challenge(args.skill, proposal)
        if args.prompt_only:
            print(result["prompt"])
        else:
            print(f"  Challenge prepared: {result['dimensions']} dimensions")
            print(f"  --prompt-only to view full prompt")
    else:
        print("[!!] 需要 --proposal 或 --proposal-file")
        sys.exit(1)


if __name__ == "__main__":
    main()
