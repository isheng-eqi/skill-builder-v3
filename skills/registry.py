#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Skill registry — discover and enumerate all skills in the system.

Provides a unified way to list, search, and filter skills.
Used by cross_skill_scan and global insight application.

v1.1: created as part of v1.1 distillation
"""

import json
from pathlib import Path
from typing import Optional


SKILLS_BASE = Path.home() / ".claude" / "skills"


def list_skills() -> list:
    """List all available skill names."""
    if not SKILLS_BASE.exists():
        return []
    skills = []
    for d in SKILLS_BASE.iterdir():
        if d.is_dir():
            has_skill_md = (d / "skill.md").exists() or (d / "SKILL.md").exists()
            if has_skill_md:
                skills.append(d.name)
    return sorted(skills)


def get_skill_info(skill_name: str) -> Optional[dict]:
    """Get structured info about a skill."""
    root = None
    for d in SKILLS_BASE.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            root = d
            break
    if not root:
        return None

    info = {
        "name": root.name,
        "path": str(root),
        "has_manifest": (root / "manifest.json").exists(),
        "has_constitution": (root / "constitution.md").exists(),
        "has_known_issues": (root / "references" / "known-issues.md").exists(),
        "has_patterns": (root / "references" / "patterns.md").exists(),
        "has_engine": (root / "engine").is_dir(),
    }

    # Load manifest if available
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            info["version"] = manifest.get("version", "unknown")
            info["description"] = manifest.get("description", "")
            info["dependencies"] = manifest.get("dependencies", {})
            info["platforms"] = manifest.get("platforms", [])
        except (json.JSONDecodeError, OSError):
            pass

    return info


def filter_skills(has_immune_system: Optional[bool] = None,
                   has_engine: Optional[bool] = None) -> list:
    """Filter skills by criteria."""
    skills = list_skills()
    result = []
    for s in skills:
        info = get_skill_info(s)
        if not info:
            continue
        if has_immune_system is not None:
            immune = info["has_known_issues"] and info["has_patterns"]
            if immune != has_immune_system:
                continue
        if has_engine is not None:
            if info["has_engine"] != has_engine:
                continue
        result.append(s)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Skill registry")
    parser.add_argument("--list", action="store_true", help="List all skills")
    parser.add_argument("--info", type=str, help="Get info about a skill")
    parser.add_argument("--filter-immune", type=str, choices=["yes", "no"],
                        help="Filter by immune system status")
    parser.add_argument("--filter-engine", type=str, choices=["yes", "no"],
                        help="Filter by engine presence")

    args = parser.parse_args()

    if args.list:
        skills = list_skills()
        for s in skills:
            info = get_skill_info(s)
            immune = "[免疫]" if (info and info["has_known_issues"] and info["has_patterns"]) else "[无免疫]"
            ver = info.get("version", "?") if info else "?"
            print(f"  {immune} {s} (v{ver})")
        print(f"\n  共 {len(skills)} 个技能")

    elif args.info:
        info = get_skill_info(args.info)
        if info:
            print(json.dumps(info, indent=2, ensure_ascii=False))
        else:
            print(f"[!!] 技能 '{args.info}' 不存在")
            sys.exit(1)

    elif args.filter_immune:
        has = args.filter_immune == "yes"
        skills = filter_skills(has_immune_system=has)
        label = "有免疫系统" if has else "无免疫系统"
        print(f"  {label}: {skills}")
        print(f"  共 {len(skills)} 个技能")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
