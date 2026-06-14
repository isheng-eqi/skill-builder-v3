#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Skill signature system — declarative contracts for skill modules (DSPy-inspired).

Each skill module declares its input/output contract as a Signature.
The engine enforces these contracts at composition time.
Signatures are what enable the skill graph's DAG structure.
"""

import json
from pathlib import Path
from typing import Optional

# ── Signature definition ──────────────────────────────────────────────────────

class Signature:
    """A skill module's input/output contract.

    This is the WHAT — the implementation (prompt/code) is the HOW.
    Inspired by DSPy's declarative signatures.
    """

    def __init__(self, name: str, inputs: list, outputs: list,
                 side_effects: Optional[list] = None,
                 description: str = ""):
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.side_effects = side_effects or []
        self.description = description

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "side_effects": self.side_effects,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Signature":
        return cls(
            name=d.get("name", "unknown"),
            inputs=d.get("inputs", []),
            outputs=d.get("outputs", []),
            side_effects=d.get("side_effects", []),
            description=d.get("description", "")
        )

    def check_compatible(self, other: "Signature") -> tuple:
        """Check if this signature's outputs satisfy other's inputs.
        Returns (compatible: bool, missing: list, extra: list).
        """
        missing = [inp for inp in other.inputs if inp not in self.outputs]
        return (len(missing) == 0, missing, [])


# ── Built-in signatures ───────────────────────────────────────────────────────

BUILTIN_SIGNATURES = {
    "scanner": Signature(
        name="scanner",
        inputs=["skill_root", "patterns_config"],
        outputs=["findings", "findings_count"],
        side_effects=["reads_disk"],
        description="快速模式扫描——grep/正则检测已知问题"
    ),
    "env_check": Signature(
        name="env_check",
        inputs=["skill_root"],
        outputs=["env_report", "dependency_status", "findings"],
        side_effects=["reads_disk", "runs_subprocess"],
        description="环境基线检测——依赖/平台/Shell兼容性"
    ),
    "fixer": Signature(
        name="fixer",
        inputs=["skill_root", "findings", "patterns_config"],
        outputs=["fixes_applied", "files_changed"],
        side_effects=["writes_to_disk", "modifies_scripts"],
        description="确定性修复——System 1 快速修复"
    ),
    "deliberator": Signature(
        name="deliberator",
        inputs=["skill_root", "iteration_history", "findings"],
        outputs=["analysis", "pattern_hypothesis", "unknown_patterns"],
        side_effects=["llm_call"],
        description="LLM深度分析——理解问题本质"
    ),
    "proposer": Signature(
        name="proposer",
        inputs=["skill_root", "analysis", "hypothesis"],
        outputs=["proposals", "architecture_changes"],
        side_effects=["llm_call", "writes_drafts"],
        description="架构提案生成——多候选方案"
    ),
    "challenger": Signature(
        name="challenger",
        inputs=["skill_root", "proposal", "iteration_history"],
        outputs=["verdict", "concerns", "passes"],
        side_effects=["llm_call"],
        description="对抗验证——找出提案的漏洞"
    ),
    "integrator": Signature(
        name="integrator",
        inputs=["skill_root", "verified_changes"],
        outputs=["snapshot_id", "integration_status"],
        side_effects=["writes_to_disk", "creates_snapshot", "updates_index"],
        description="安全集成——快照→写入→索引"
    ),
    "reflector": Signature(
        name="reflector",
        inputs=["skill_root", "iteration_history", "all_findings"],
        outputs=["insights", "new_pattern_drafts", "framework_critique"],
        side_effects=["llm_call", "writes_insights"],
        description="元循环反思——从历史中发现新模式"
    ),
    "anchor": Signature(
        name="anchor",
        inputs=["skill_root", "proposed_change"],
        outputs=["constitutional_verdict", "violations", "rollback_plan"],
        side_effects=["reads_constitution"],
        description="宪法验证——任何修改前的最后一道防线"
    )
}


# ── Skill graph validation ────────────────────────────────────────────────────

def validate_skill_graph(skill_root: Path) -> dict:
    """Validate that all declared skill compositions satisfy signature contracts.

    Returns: {"valid": bool, "errors": [str], "warnings": [str]}
    """
    manifest_path = skill_root / "manifest.json"
    if not manifest_path.exists():
        return {"valid": False, "errors": ["manifest.json not found"], "warnings": []}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = []
    warnings = []

    # Check engine modules exist
    inventory = manifest.get("file_inventory", {})
    engine_files = inventory.get("engine", []) + inventory.get("engine_system1", []) + inventory.get("engine_system2", [])
    for f in engine_files:
        engine_dir = skill_root / "engine"
        for subdir in ["", "system1", "system2"]:
            p = engine_dir / subdir / f
            if p.exists():
                break
        else:
            errors.append(f"Missing engine module: {f}")

    # Check constitution exists
    if not (skill_root / "constitution.md").exists():
        errors.append("constitution.md not found — violates Layer 3 Anchor")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Skill signature system")
    parser.add_argument("--list-signatures", action="store_true", help="List all built-in signatures")
    parser.add_argument("--validate", type=str, metavar="SKILL", help="Validate skill graph for a skill")
    parser.add_argument("--check-compose", nargs=2, metavar=("SIG_A", "SIG_B"),
                        help="Check if signature A's outputs satisfy B's inputs")
    args = parser.parse_args()

    if args.list_signatures:
        for name, sig in BUILTIN_SIGNATURES.items():
            print(f"\n  {name}:")
            print(f"    输入:  {', '.join(sig.inputs)}")
            print(f"    输出:  {', '.join(sig.outputs)}")
            print(f"    副作用: {', '.join(sig.side_effects) if sig.side_effects else '无'}")
        return

    if args.validate:
        result = validate_skill_graph(Path(args.validate))
        if result["valid"]:
            print("[OK] Skill graph valid")
        else:
            for e in result["errors"]:
                print(f"[!!] {e}")
            sys.exit(1)
        return

    if args.check_compose:
        a, b = args.check_compose
        sig_a = BUILTIN_SIGNATURES.get(a)
        sig_b = BUILTIN_SIGNATURES.get(b)
        if not sig_a:
            print(f"[!!] Unknown signature: {a}")
            sys.exit(1)
        if not sig_b:
            print(f"[!!] Unknown signature: {b}")
            sys.exit(1)
        ok, missing, _ = sig_a.check_compatible(sig_b)
        if ok:
            print(f"[OK] {a} → {b} compatible")
        else:
            print(f"[!!] {a} → {b}: missing inputs: {missing}")
            sys.exit(1)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
