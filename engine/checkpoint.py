#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""P10: Checkpoint — 崩溃恢复 (Claude Code 蒸馏).

v1.2 (2026-06-13): 蒸馏自 Claude Code 仅追加会话存储 + 从任意点恢复模式.

核心机制:
  1. 每轮循环开始前写入 data/loop_state.json (checkpoint)
  2. 记录: 当前轮数, 剩余发现, 活跃 proposals, fix_fingerprints, metrics 快照
  3. run_loop() 启动时检测是否有未完成的 loop → 自动 resume
  4. loop 正常完成后清除 checkpoint

Claude Code 的关键洞察:
  512K行代码中最有价值的部分不是 while-true-loop，而是
  "crash → resume from any point" 的容错基础设施。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Paths ──────────────────────────────────────────────────────────────────────

def _checkpoint_path(skill_name: str) -> Optional[Path]:
    """Get the checkpoint file path for a skill."""
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            cp_dir = d / "data"
            cp_dir.mkdir(parents=True, exist_ok=True)
            return cp_dir / "loop_state.json"
    return None


# ── Save checkpoint ────────────────────────────────────────────────────────────

def save_checkpoint(skill_name: str, round_num: int,
                     state: dict, verbose: bool = False) -> bool:
    """Save loop state before each round.

    Args:
        skill_name: 目标 skill
        round_num: 当前轮数
        state: {findings_count, consecutive_zero, fix_fingerprints, proposals_active, ...}

    The checkpoint is a complete snapshot that allows resuming from this point.
    """
    cp_path = _checkpoint_path(skill_name)
    if not cp_path:
        return False

    # Enrich with metadata
    checkpoint = {
        "skill_name": skill_name,
        "round_num": round_num,
        "iso_saved": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "state": state,
        # Include recent metrics snapshot for recovery context
        "metrics_snapshot": _snapshot_metrics(skill_name),
        "active_rules_count": _count_active_rules(skill_name),
    }

    cp_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False),
                       encoding="utf-8")

    if verbose:
        print(f"  [CHECKPOINT] 已保存: round={round_num}, "
              f"findings={state.get('findings_count', '?')}")

    return True


def save_checkpoint_final(skill_name: str, round_num: int,
                           state: dict, verbose: bool = False) -> bool:
    """Save final checkpoint after loop completes. Marks status='completed'
    and includes convergence state. (v1.2: fixes stale loop_state.json bug)
    """
    cp_path = _checkpoint_path(skill_name)
    if not cp_path:
        return False

    checkpoint = {
        "skill_name": skill_name,
        "round_num": round_num,
        "iso_saved": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "state": state,
        "metrics_snapshot": _snapshot_metrics(skill_name),
        "active_rules_count": _count_active_rules(skill_name),
        "iso_completed": datetime.now(timezone.utc).isoformat(),
    }

    cp_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False),
                       encoding="utf-8")

    if verbose:
        converged = state.get("converged", False)
        print(f"  [CHECKPOINT-FINAL] round={round_num}, "
              f"converged={converged}, findings={state.get('findings_count', '?')}")

    return True


# ── Resume from checkpoint ─────────────────────────────────────────────────────

def detect_incomplete_loop(skill_name: str) -> Optional[dict]:
    """Check if there's an incomplete loop that needs resuming.

    Returns the checkpoint dict if found, None if nothing to resume.
    """
    cp_path = _checkpoint_path(skill_name)
    if not cp_path or not cp_path.exists():
        return None

    try:
        checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))
        if checkpoint.get("status") == "in_progress":
            return checkpoint
    except (json.JSONDecodeError, OSError):
        return None

    return None


def resume_from_checkpoint(skill_name: str) -> dict:
    """Resume an incomplete loop from the last checkpoint.

    Returns:
      {resumed: bool, checkpoint: dict, actions: list}

    The caller (loop.py) reads this and:
      1. Restores fix_fingerprints for TAPO tracking
      2. Restores round_num to continue from
      3. Restores consecutive_zero for convergence detection
      4. Logs a resume event
    """
    checkpoint = detect_incomplete_loop(skill_name)
    if not checkpoint:
        return {"resumed": False, "reason": "无未完成的 loop 需要恢复"}

    from engine import memory

    round_num = checkpoint.get("round_num", 1)
    state = checkpoint.get("state", {})

    # Log resume
    memory.write_event(skill_name, "loop", {
        "phase": "resume_from_checkpoint",
        "round_num": round_num,
        "last_saved_iso": checkpoint.get("iso_saved", "?"),
        "state_summary": {
            "findings_count": state.get("findings_count", "?"),
            "consecutive_zero": state.get("consecutive_zero", "?"),
            "fixes_applied_so_far": len(state.get("fix_fingerprints", []))
        }
    })

    return {
        "resumed": True,
        "checkpoint": checkpoint,
        "round_num": round_num,
        "state": state,
        "fix_fingerprints": state.get("fix_fingerprints", []),
        "consecutive_zero": state.get("consecutive_zero", 0),
        "actions": [
            f"从 round {round_num} 恢复",
            f"已恢复 {len(state.get('fix_fingerprints', []))} 条 TAPO 修复指纹",
            f"连续零发现: {state.get('consecutive_zero', 0)}"
        ]
    }


def clear_checkpoint(skill_name: str) -> bool:
    """Mark checkpoint as completed (or delete it)."""
    cp_path = _checkpoint_path(skill_name)
    if not cp_path or not cp_path.exists():
        return True  # Nothing to clear

    try:
        checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))
        checkpoint["status"] = "completed"
        checkpoint["iso_completed"] = datetime.now(timezone.utc).isoformat()
        cp_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        return True
    except (json.JSONDecodeError, OSError):
        # Corrupt checkpoint — delete
        cp_path.unlink(missing_ok=True)
        return True


# ── Helpers ────────────────────────────────────────────────────────────────────

def _snapshot_metrics(skill_name: str) -> dict:
    """Snapshot current metrics for checkpoint context."""
    from engine import memory

    return {
        "convergence_rounds": memory.get_latest_metric(skill_name, "convergence_rounds", -1),
        "total_findings": memory.get_latest_metric(skill_name, "total_findings", -1),
        "generator_noise": memory.get_latest_metric(skill_name, "generator_noise", -1),
        "verifier_snr": memory.get_latest_metric(skill_name, "verifier_snr", -1),
        "gvu": memory.calculate_gvu_snr(skill_name),
    }


def _count_active_rules(skill_name: str) -> int:
    """Count active rules (hardcoded + generated)."""
    from engine.system1 import rules as rule_engine
    applicable = rule_engine.get_applicable_rules([], skill_name)
    return len(applicable)


# ── Recovery strategies ────────────────────────────────────────────────────────

def diagnose_crash(skill_name: str) -> dict:
    """Post-crash diagnosis: what likely caused the crash?

    Analyzes last checkpoint + recent events to determine:
      - Was it a Python error? (Traceback in event logs)
      - Was it a timeout? (No events for >1 hour)
      - Was it a user interrupt? (Last event is a scan, not a fix)
      - Was it a GVU instability? (SNR plummeting before crash)
    """
    from engine import memory

    checkpoint = detect_incomplete_loop(skill_name)
    events = memory.read_events(skill_name, limit=20)

    causes = []
    actions = []

    if checkpoint:
        # Check if GVU was unstable before crash
        gvu_snapshot = checkpoint.get("metrics_snapshot", {}).get("gvu", {})
        if not gvu_snapshot.get("stable", True):
            causes.append("GVU 不稳定 (SNR 下降)")
            actions.append("增加 gvu_snr_threshold 或减少 exec_trace_sample_rate")

        # Check time since last checkpoint
        try:
            last_saved = datetime.fromisoformat(checkpoint["iso_saved"])
            now = datetime.now(timezone.utc)
            gap_hours = (now - last_saved).total_seconds() / 3600
            if gap_hours > 1:
                causes.append(f"上次checkpoint距今 {gap_hours:.1f}h")
                actions.append("可能不是crash——loop被外部终止")
        except (ValueError, KeyError):
            pass  # TODO: log or re-raise

    # Check recent events for errors
    for e in events[:5]:
        data = e.get("data", {})
        if data.get("status") == "failed" or "error" in str(data).lower()[:50]:
            causes.append(f"最近事件含错误: {e['event_type']}")
            actions.append("检查该事件的详情确定根因")

    return {
        "crashed": checkpoint is not None,
        "checkpoint_available": checkpoint is not None,
        "likely_causes": causes or ["未知原因"],
        "recovery_actions": actions or ["重新运行 loop.py — checkpoint 自动检测并恢复"],
        "resume_possible": checkpoint is not None
    }


# ── Integration with loop.py ───────────────────────────────────────────────────

def checkpoint_wrapper(skill_name: str, round_num: int,
                        loop_state: dict) -> bool:
    """Convenience wrapper: save checkpoint + handle resume detection.

    Called at the START of each round in run_loop().
    Automatically handles first-round resume detection.
    """
    state = {
        "findings_count": loop_state.get("findings_count", -1),
        "consecutive_zero": loop_state.get("consecutive_zero", 0),
        "fix_fingerprints": loop_state.get("fix_fingerprints", []),
        "proposals_active": loop_state.get("proposals_active", 0),
        "total_fixes_applied": loop_state.get("total_fixes_applied", 0),
        "iso_started": loop_state.get("iso_started",
                                       datetime.now(timezone.utc).isoformat())
    }
    return save_checkpoint(skill_name, round_num, state)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="P10 Checkpoint (Claude Code)——崩溃恢复"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("save", help="保存 checkpoint")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--round", type=int, default=1, help="当前轮数")
    p.add_argument("--state", default="{}", help="JSON 状态")

    p = sub.add_parser("detect", help="检测未完成的 loop")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("resume", help="从 checkpoint 恢复")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("clear", help="清除 checkpoint")
    p.add_argument("skill", help="目标 skill")

    p = sub.add_parser("diagnose", help="崩溃诊断")
    p.add_argument("skill", help="目标 skill")

    args = parser.parse_args()

    if args.cmd == "save":
        state = json.loads(args.state)
        ok = save_checkpoint(args.skill, args.round, state, verbose=True)
        print(f"[{'OK' if ok else '!!'}] Checkpoint saved")

    elif args.cmd == "detect":
        cp = detect_incomplete_loop(args.skill)
        if cp:
            print(f"[!!] 发现未完成 loop: round={cp.get('round_num', '?')}")
            print(f"  保存时间: {cp.get('iso_saved', '?')}")
        else:
            print("[OK] 无未完成 loop")

    elif args.cmd == "resume":
        result = resume_from_checkpoint(args.skill)
        if result["resumed"]:
            print(f"[OK] 恢复成功: round={result['round_num']}")
            for a in result["actions"]:
                print(f"  {a}")
        else:
            print(f"[--] {result['reason']}")

    elif args.cmd == "clear":
        ok = clear_checkpoint(args.skill)
        print(f"[{'OK' if ok else '!!'}] Checkpoint cleared")

    elif args.cmd == "diagnose":
        diag = diagnose_crash(args.skill)
        print(f"  崩溃检测: {'[!!] 发现' if diag['crashed'] else '[OK] 无'}")
        if diag["crashed"]:
            print(f"  可能原因: {diag['likely_causes']}")
            print(f"  恢复建议: {diag['recovery_actions']}")
            print(f"  可恢复: {'是' if diag['resume_possible'] else '否'}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
