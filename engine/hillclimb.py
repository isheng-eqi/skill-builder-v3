#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P9: Hill-Climb — 统一适应度爬山优化 (Karpathy Autoresearch 蒸馏).

v1.2 (2026-06-13): 蒸馏自 Karpathy autoresearch 单指标爬山策略.

核心洞见:
  Karpathy proved you can auto-improve complex systems with ONE metric (val_bpb)
  and naive hill-climbing. No gradient, no Bayesian optimization, no RL —
  just: try a change → measure the single metric → keep if improved.

自进化引擎的 unified_fitness:
  - 目标: MAXIMIZE (越高越好)
  - 公式: (1 - normalized_findings) × W1 + verifier_snr × W2 + trace_pass_rate × W3 + convergence_speed × W4
  - 调整 design_params → 测量 fitness 变化 → 保留改进

每 N 轮 loop 后自动运行一次 hill-climb，微调以下参数:
  - stop_rounds: 收敛阈值
  - meta_audit_interval: 元审计间隔
  - gvu_snr_threshold: GVU 门控阈值
  - exec_trace_sample_rate: GEPA 验证采样率
  - archive_interval: DGM 存档间隔
"""

import json
import os
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Fitness weights ────────────────────────────────────────────────────────────

DEFAULT_WEIGHTS = {
    "w_findings": 1.0,       # Fewer findings = better
    "w_snr": 2.0,            # Higher verifier SNR = much better
    "w_trace": 1.5,          # Higher trace pass rate = better
    "w_convergence": 0.5,    # Faster convergence = slightly better
    "w_evidence": 1.0,       # Higher evidence pass rate = better
}


# ── Fitness calculation ────────────────────────────────────────────────────────

def calculate_unified_fitness(skill_name: str,
                               weights: Optional[dict] = None) -> float:
    """Calculate the unified fitness score for a skill.

    Single metric that drives ALL optimization decisions.
    Higher = better. Range: typically [-5, 10+].

    Components:
      1. findings_normalized: 0 = worst (many findings), 1 = best (zero findings)
      2. verifier_snr: GVU stability ratio
      3. trace_pass_rate: GEPA execution trace success rate
      4. convergence_speed: how fast did the last loop converge?
      5. evidence_pass_rate: P2 evidence chain quality
    """
    from engine import memory

    w = weights or DEFAULT_WEIGHTS

    # Component 1: Findings count (inverted and normalized)
    last_scan_metric = memory.get_latest_metric(skill_name, "total_findings", 0)
    # Normalize: 0 findings = 1.0, 20+ findings = 0.0
    findings_normalized = max(0.0, 1.0 - last_scan_metric / 20.0)

    # Component 2: Verifier SNR
    gvu = memory.calculate_gvu_snr(skill_name)
    verifier_snr = gvu.get("verifier_snr", 0.0)
    if verifier_snr is None:
        verifier_snr = 0.5  # No data — neutral score

    # Component 3: Trace pass rate
    trace_events = memory.read_events(skill_name, limit=50)
    traces = [e for e in trace_events if e["event_type"] == "execution_trace"]
    if traces:
        passed = sum(1 for t in traces if t.get("data", {}).get("success", False))
        trace_pass_rate = passed / len(traces)
    else:
        trace_pass_rate = 1.0  # No data = assume good

    # Component 4: Convergence speed
    convergence_metric = memory.get_latest_metric(
        skill_name, "convergence_rounds", 999
    )
    # Normalize: 2 rounds = 1.0 (perfect), 10+ rounds = 0.0
    convergence_speed = max(0.0, 1.0 - (convergence_metric - 2) / 8.0)

    # Component 5: Evidence pass rate (P2)
    evidence_events = [e for e in trace_events if e["event_type"] == "evidence_span"]
    if evidence_events:
        ev_passed = sum(1 for e in evidence_events
                       if e.get("data", {}).get("verdict") == "evidence_consistent")
        evidence_pass_rate = ev_passed / len(evidence_events)
    else:
        evidence_pass_rate = 0.5  # No data = neutral

    # Weighted sum
    fitness = (
        findings_normalized * w["w_findings"] +
        verifier_snr * w["w_snr"] +
        trace_pass_rate * w["w_trace"] +
        convergence_speed * w["w_convergence"] +
        evidence_pass_rate * w["w_evidence"]
    )

    # Record fitness metric
    memory.write_metric(skill_name, "unified_fitness", fitness, {
        "findings": round(findings_normalized, 3),
        "snr": round(verifier_snr, 3),
        "trace": round(trace_pass_rate, 3),
        "convergence": round(convergence_speed, 3),
        "evidence": round(evidence_pass_rate, 3)
    })

    return round(fitness, 4)


# ── Parameter perturbation ─────────────────────────────────────────────────────

PARAM_SPACE = {
    "stop_rounds": {"type": "int", "min": 1, "max": 5, "default": 2,
                    "step": 1, "description": "连续零发现轮数后停止"},
    "meta_audit_interval": {"type": "int", "min": 3, "max": 15, "default": 5,
                            "step": 1, "description": "触发 System 2 的轮间隔"},
    "max_iterations": {"type": "int", "min": 5, "max": 30, "default": 10,
                       "step": 2, "description": "最大迭代轮数"},
    "gvu_snr_threshold": {"type": "float", "min": 0.5, "max": 2.0, "default": 1.0,
                          "step": 0.1, "description": "GVU SNR 阈值"},
    "exec_trace_sample_rate": {"type": "float", "min": 0.3, "max": 1.0, "default": 1.0,
                               "step": 0.1, "description": "GEPA 验证采样率"},
    "archive_interval": {"type": "int", "min": 5, "max": 30, "default": 10,
                         "step": 5, "description": "DGM 存档间隔"},
}


def _load_params_file(skill_name: str) -> Optional[Path]:
    """Find the design_params.json for a skill."""
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            dp = d / "design_params.json"
            return dp if dp.exists() else None
    return None


def perturb_param(current_value, param_spec: dict, direction: int) -> float:
    """Perturb a single parameter. direction: +1 (increase) or -1 (decrease)."""
    step = param_spec["step"]
    new_val = current_value + direction * step
    new_val = max(param_spec["min"], min(param_spec["max"], new_val))
    if param_spec["type"] == "int":
        new_val = int(new_val)
    else:
        new_val = round(new_val, 1)
    return new_val


def try_perturbation(skill_name: str, param_name: str,
                      current_value, param_spec: dict,
                      direction: int) -> dict:
    """Try perturbing one parameter and measure fitness delta.

    Returns {param, old, new, fitness_delta, improved}.
    """
    from engine import memory

    new_value = perturb_param(current_value, param_spec, direction)
    if new_value == current_value:
        return {"param": param_name, "old": current_value, "new": new_value,
                "fitness_delta": 0, "improved": False, "reason": "at boundary"}

    # Measure baseline fitness
    baseline_fitness = calculate_unified_fitness(skill_name)

    # Apply the perturbation (write to design_params.json)
    params_path = _load_params_file(skill_name)
    if not params_path:
        # Update manifest.json instead
        base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
        for d in base.iterdir():
            if d.is_dir() and d.name.lower() == skill_name.lower():
                mf = d / "manifest.json"
                if mf.exists():
                    params_path = mf
                    break

    if not params_path:
        return {"param": param_name, "error": "no params file found"}

    # Save current value and apply new
    old_params = json.loads(params_path.read_text(encoding="utf-8"))
    if params_path.name == "design_params.json":
        if "params" not in old_params:
            old_params["params"] = {}
        if param_name not in old_params["params"]:
            old_params["params"][param_name] = {"value": current_value}
        old_params["params"][param_name]["value"] = new_value
        old_params["params"][param_name]["_last_hillclimb"] = (
            datetime.now(timezone.utc).isoformat()
        )
    else:
        # manifest.json
        if "design_params" not in old_params:
            old_params["design_params"] = {}
        old_params["design_params"][param_name] = new_value

    params_path.write_text(json.dumps(old_params, indent=2, ensure_ascii=False),
                           encoding="utf-8")

    # Re-measure fitness (would be after running a loop in production — here we approximate)
    memory.write_event(skill_name, "loop", {
        "phase": "hillclimb_perturbation",
        "param": param_name,
        "old": current_value,
        "new": new_value,
        "direction": direction
    })

    # For now, approximate fitness delta based on param semantics
    # In production, this would run a full loop and re-measure
    delta = _approximate_fitness_delta(param_name, current_value, new_value,
                                        param_spec)

    improved = delta > 0

    if not improved:
        # Rollback: restore old value
        if params_path.name == "design_params.json":
            old_params["params"][param_name]["value"] = current_value
        else:
            old_params["design_params"][param_name] = current_value
        params_path.write_text(json.dumps(old_params, indent=2, ensure_ascii=False),
                               encoding="utf-8")

    return {
        "param": param_name,
        "old": current_value,
        "new": new_value if improved else current_value,
        "fitness_delta": round(delta, 4),
        "improved": improved,
        "direction": "increase" if direction > 0 else "decrease"
    }


def _approximate_fitness_delta(param_name: str, old_val, new_val,
                                param_spec: dict) -> float:
    """v4 诚实声明: 返回 0.0（无变化）——在没有实际 fitness 测量时不做假优化。

    旧版 (v3) 行为: 返回硬编码常量 (-0.1, 0.05, -0.05...)，
    仅基于参数名和方向，从未实际运行 loop 测量真实 fitness 变化。
    这导致 hillclimb 优化的是幻象——硬编码常量的 if-elif 链声称在爬山，
    实际在优化的函数是假的。

    v4 修正: 当没有实际测量数据时返回 0.0。
    调用者 (run_hillclimb) 可以据此决定是否信任这个 delta。

    当 fitness 数据可用时，应使用真实测量而非此函数。
    当数据不可用时，诚实承认无法测量，返回 0.0。

    每个参数的真实方向 (保留作为文档——供将来重写使用):
      - stop_rounds: ↓ 更快收敛但风险假收敛
      - meta_audit_interval: ↓ 更频繁 System 2 = 更多洞察但更多 token 开销
      - max_iterations: ↑ 更彻底但更耗时
      - gvu_snr_threshold: ↑ 更保守但可能阻止合法修复
      - exec_trace_sample_rate: ↓ 验证少但计算开销低 (应 ↑)
      - archive_interval: ↓ 更多快照 = 更多存储但更好回滚
    """
    # v4: No measurement → no fake delta. Return neutral.
    # When real unified_fitness metrics become available, compute actual delta from them.
    return 0.0


# ── Full hill-climb cycle ─────────────────────────────────────────────────────

def run_hillclimb(skill_name: str, max_perturbations: int = 5) -> dict:
    """Run one hill-climb cycle: try perturbations, keep improvements.

    This is the Karpathy autoresearch loop applied to design_params:
      for each param:
        try +step → if fitness improves, keep it
        try -step → if fitness improves, keep it
      → repeat until no more improvements or max_perturbations reached
    """
    from engine import memory

    # v4 gate: 检查是否有真实 fitness 测量数据。
    # 如果没有 unified_fitness 指标（cold_start），delta 全是 0.0，
    # hillclimb 只会浪费时间尝试并回滚。直接跳过。
    fitness_metrics = memory.read_metric_series(skill_name, "unified_fitness")
    if len(fitness_metrics) < 5:
        return {
            "error": None,
            "skipped": True,
            "improvements": 0,
            "verdict": "[--] Hillclimb 跳过——无足够 unified_fitness 测量数据。"
                       " 需先积累至少 5 轮真实 loop 运行产生的 fitness 数据。"
                       " (v4 诚实声明: 旧版在此条件下仍会优化——用的是硬编码常量，不是真实测量。)"
        }

    memory.write_event(skill_name, "loop", {"phase": "hillclimb_start"})

    baseline = calculate_unified_fitness(skill_name)
    params_path = _load_params_file(skill_name)

    if not params_path:
        return {"error": "design_params.json or manifest.json not found"}

    # Load current params
    params_data = json.loads(params_path.read_text(encoding="utf-8"))
    if params_path.name == "design_params.json":
        current_params = {
            k: v.get("value", v) if isinstance(v, dict) else v
            for k, v in params_data.get("params", {}).items()
        }
    else:
        current_params = params_data.get("design_params", {})

    improvements = []
    total_perturbations = 0

    for param_name, param_spec in PARAM_SPACE.items():
        if total_perturbations >= max_perturbations:
            break

        current_val = current_params.get(param_name, param_spec["default"])

        # Try increase
        result = try_perturbation(skill_name, param_name, current_val,
                                   param_spec, +1)
        total_perturbations += 1
        if result.get("improved"):
            improvements.append(result)
            current_params[param_name] = result["new"]
            continue

        if total_perturbations >= max_perturbations:
            break

        # Try decrease
        result = try_perturbation(skill_name, param_name, current_val,
                                   param_spec, -1)
        total_perturbations += 1
        if result.get("improved"):
            improvements.append(result)
            current_params[param_name] = result["new"]

    # Final fitness
    final_fitness = calculate_unified_fitness(skill_name)
    fitness_delta = final_fitness - baseline

    memory.write_event(skill_name, "loop", {
        "phase": "hillclimb_end",
        "baseline_fitness": baseline,
        "final_fitness": final_fitness,
        "fitness_delta": fitness_delta,
        "improvements": len(improvements),
        "perturbations": total_perturbations
    })

    return {
        "skill": skill_name,
        "baseline_fitness": baseline,
        "final_fitness": final_fitness,
        "fitness_delta": round(fitness_delta, 4),
        "improvements": improvements,
        "total_perturbations": total_perturbations,
        "verdict": (
            f"[OK] 适应度 {baseline:.4f} → {final_fitness:.4f} "
            f"(Δ={fitness_delta:+.4f}, {len(improvements)} 项改进)"
        )
    }


# ── Auto-tune trigger ──────────────────────────────────────────────────────────

def should_hillclimb(skill_name: str, min_rounds: int = 10,
                      fitness_stagnation: int = 5) -> bool:
    """Check if we should auto-trigger hillclimb.

    Triggers when:
      - At least min_rounds of loop history exists
      - Fitness has not improved in the last fitness_stagnation measurements
    """
    from engine import memory

    metrics = memory.read_metric_series(skill_name, "unified_fitness")
    if len(metrics) < min_rounds:
        return False

    recent = metrics[-fitness_stagnation:]
    if len(recent) < fitness_stagnation:
        return False

    values = [m["value"] for m in recent]
    first_half = sum(values[:len(values)//2]) / max(1, len(values)//2)
    second_half = sum(values[len(values)//2:]) / max(1, len(values) - len(values)//2)

    return second_half <= first_half  # Stagnation or decline


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P9 Hill-Climb (Karpathy)——统一适应度爬山优化"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("fitness", help="计算当前统一适应度")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("climb", help="运行爬山优化")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--max-perturbations", type=int, default=5,
                   help="最大扰动次数 (默认5)")

    p = sub.add_parser("check", help="检查是否应该触发爬山")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--stagnation", type=int, default=5,
                   help="停滞检测阈值")

    p = sub.add_parser("params", help="显示参数空间")

    args = parser.parse_args()

    if args.cmd == "fitness":
        fitness = calculate_unified_fitness(args.skill)
        print(f"  统一适应度: {fitness}")
        print(f"  评级: {'优秀' if fitness > 3 else '良好' if fitness > 2 else '一般' if fitness > 1 else '需改进'}")
    elif args.cmd == "climb":
        result = run_hillclimb(args.skill, args.max_perturbations)
        if "error" in result:
            print(f"[!!] {result['error']}")
            sys.exit(1)
        print(f"  {result['verdict']}")
        for imp in result["improvements"]:
            print(f"    [{imp['param']}] {imp['old']} → {imp['new']} "
                  f"(Δ={imp['fitness_delta']:+.4f}, {imp.get('direction', '?')})")
    elif args.cmd == "check":
        if should_hillclimb(args.skill, fitness_stagnation=args.stagnation):
            print(f"[!!] 适应度停滞——建议运行 hill-climb")
        else:
            print(f"[OK] 适应度仍在改善中")
    elif args.cmd == "params":
        for name, spec in PARAM_SPACE.items():
            print(f"  {name}: {spec['type']} [{spec['min']}, {spec['max']}] "
                  f"step={spec['step']} default={spec['default']}")
            print(f"    {spec['description']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
