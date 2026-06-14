#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 2: Proposer — generates architecture change proposals.

After deliberation identifies what needs to change, the Proposer
generates concrete, verifiable proposals. Each proposal includes:
  - What files to change
  - The exact nature of the change
  - Pre-conditions and post-conditions
  - Rollback plan

Proposals are written to data/proposals/ for human or system approval.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from engine.platform import find_skill_root as _find_skill_root
def create_proposal(skill_name: str, title: str, description: str,
                    files_changed: list, change_type: str,
                    rationale: str, confidence: float = 0.5,
                    deliberation_id: Optional[str] = None) -> dict:
    """Create a structured proposal."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    proposal_id = f"prop-{ts}"

    proposal = {
        "proposal_id": proposal_id,
        "iso_created": datetime.now(timezone.utc).isoformat(),
        "skill": skill_name,
        "title": title,
        "description": description,
        "change_type": change_type,
        "files_changed": files_changed,
        "rationale": rationale,
        "confidence": confidence,
        "deliberation_id": deliberation_id,
        "status": "draft",
        "verification": None,
        "approval": None
    }
    return proposal


def save_proposal(skill_name: str, proposal: dict) -> Optional[Path]:
    """Save proposal to data/proposals/."""
    root = _find_skill_root(skill_name)
    if not root:
        return None
    proposals_dir = root / "data" / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    fp = proposals_dir / f"{proposal['proposal_id']}.json"
    fp.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def load_proposal(skill_name: str, proposal_id: str) -> Optional[dict]:
    """Load a proposal from disk."""
    root = _find_skill_root(skill_name)
    if not root:
        return None
    for path in [root / "data" / "proposals" / f"{proposal_id}.json",
                 root / "data" / "proposals" / proposal_id]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
    return None


def list_proposals(skill_name: str, status: Optional[str] = None) -> list:
    """List proposals, newest first."""
    root = _find_skill_root(skill_name)
    if not root:
        return []
    proposals_dir = root / "data" / "proposals"
    if not proposals_dir.exists():
        return []
    proposals = []
    for fp in sorted(proposals_dir.glob("prop-*.json"), reverse=True):
        try:
            p = json.loads(fp.read_text(encoding="utf-8"))
            if status and p.get("status") != status:
                continue
            proposals.append(p)
        except (json.JSONDecodeError, OSError):
            continue
    return proposals


def update_proposal_status(skill_name: str, proposal_id: str,
                           new_status: str, meta: Optional[dict] = None) -> bool:
    """Update proposal status."""
    proposal = load_proposal(skill_name, proposal_id)
    if not proposal:
        return False
    proposal["status"] = new_status
    if meta:
        key = {"verified": "verification", "approved": "approval",
               "applied": "applied", "rejected": "rejection"}.get(new_status, "meta")
        proposal[key] = {"iso": datetime.now(timezone.utc).isoformat(), **(meta or {})}
    return save_proposal(skill_name, proposal) is not None


def propose_new_pattern(skill_name: str, pattern_name: str,
                         symptoms: str, affected_files: str,
                         countermeasure: str, check_command: str,
                         rationale: str) -> dict:
    """Generate a new pattern proposal and write to patterns-drafts.md."""
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在"}

    drafts_path = root / "references" / "patterns-drafts.md"
    if not drafts_path.exists():
        drafts_path.write_text(
            "# 模式草案 — System 2 自动发现\n\n"
            "> 本文件由 System 2 自动生成。草案积累到证据阈值后，\n"
            "> 经人工审批可晋级为正式模式（patterns.md）。\n\n",
            encoding="utf-8"
        )

    ts = datetime.now(timezone.utc)
    draft_entry = f"""
---

## 模式草案: {pattern_name}

<!-- @proposed iso="{ts.isoformat()}" confidence="0.5" -->

**症状:** {symptoms}

**受影响的文件类型:** {affected_files}

**对策:** {countermeasure}

**检查命令:** `{check_command}`

**提出理由:** {rationale}

**状态:** 待审批
"""
    with open(drafts_path, "a", encoding="utf-8") as f:
        f.write(draft_entry)

    # Record in evolution log
    evo_path = root / "references" / "pattern-evolution.jsonl"
    evo_entry = json.dumps({
        "iso": ts.isoformat(), "event": "proposed",
        "pattern_name": pattern_name, "source": "system2"
    }, ensure_ascii=False)
    with open(evo_path, "a", encoding="utf-8") as f:
        f.write(evo_entry + "\n")

    proposal = create_proposal(
        skill_name=skill_name, title=f"新模式: {pattern_name}",
        description=f"症状: {symptoms}\n\n对策: {countermeasure}",
        files_changed=["references/patterns-drafts.md", "references/pattern-evolution.jsonl"],
        change_type="new-pattern", rationale=rationale
    )
    save_proposal(skill_name, proposal)

    return {"proposal_id": proposal["proposal_id"], "pattern_name": pattern_name,
            "drafts_file": str(drafts_path), "status": "draft"}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="System 2 提案生成器")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("new-pattern", help="提案新模式")
    p.add_argument("skill")
    p.add_argument("--name", required=True)
    p.add_argument("--symptoms", default="")
    p.add_argument("--affected", default="scripts/*.py")
    p.add_argument("--countermeasure", default="")
    p.add_argument("--check", default="grep -rn 'pattern' .")
    p.add_argument("--rationale", default="System 2 自动发现")

    p = sub.add_parser("list", help="列出提案")
    p.add_argument("skill")
    p.add_argument("--status")

    p = sub.add_parser("show", help="显示提案")
    p.add_argument("skill")
    p.add_argument("proposal_id")

    p = sub.add_parser("update", help="更新提案状态")
    p.add_argument("skill")
    p.add_argument("proposal_id")
    p.add_argument("--status", required=True,
                   choices=["draft", "verified", "approved", "applied", "rejected"])
    p.add_argument("--reason", default="")

    args = parser.parse_args()

    if args.cmd == "new-pattern":
        result = propose_new_pattern(
            args.skill, args.name, args.symptoms,
            args.affected, args.countermeasure, args.check, args.rationale
        )
        if "error" in result:
            print(f"[!!] {result['error']}")
            sys.exit(1)
        print(f"[OK] 新模式提案: {result['proposal_id']}")

    elif args.cmd == "list":
        proposals = list_proposals(args.skill, args.status)
        for p in proposals:
            icons = {"draft": "[?]", "verified": "[OK]", "approved": "[+]",
                     "applied": "[✓]", "rejected": "[✗]"}
            print(f"  {icons.get(p.get('status', ''), '[?]')} "
                  f"{p['proposal_id']} [{p.get('change_type', '?')}] "
                  f"{p.get('title', '?')[:80]}")
        if not proposals:
            print("  (无提案)")

    elif args.cmd == "show":
        p = load_proposal(args.skill, args.proposal_id)
        if p:
            print(json.dumps(p, indent=2, ensure_ascii=False))
        else:
            print(f"[!!] 提案不存在")
            sys.exit(1)

    elif args.cmd == "update":
        ok = update_proposal_status(args.skill, args.proposal_id, args.status,
                                     {"reason": args.reason} if args.reason else None)
        print(f"[{'OK' if ok else '!!'}] {args.proposal_id} → {args.status}")
        if not ok:
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
