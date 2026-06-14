#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 1: Patterns — known pattern matching against a skill.

Reads patterns.md (and patterns-drafts.md) and matches a skill's files
against the defined patterns. Unlike scanner.py (regex-based), this
performs structural analysis at the pattern level.

v1.1 additions (2026-06-13):
  - Cross-skill pattern bootstrap: apply global insights from other skills
"""

from pathlib import Path
from typing import Optional

# Add engine to path for sibling imports when running standalone
HERE = Path(__file__).resolve().parent
if str(HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent))


# ── Path helpers ──────────────────────────────────────────────────────────────
from engine.platform import find_skill_root as _find_skill_root
# ── Pattern matching ──────────────────────────────────────────────────────────

def check_pattern_distributed_state(root: Path) -> list:
    """Pattern 1: Check for concepts scattered across files.

    Looks for key strings that appear in both .py and .md files,
    indicating possible distributed state.
    """
    findings = []
    # Key concepts that should be centralized
    # Excluded from this list: known-issues, changelog, patterns.md — these ARE the
    # centralized recording artifacts, not symptoms of distributed state
    concepts = [
        "design_params", "immune_system",
    ]

    # Exclude data/ (snapshots, logs, archives) — they duplicate source files and inflate counts
    EXCLUDED_DIRS = {"data", "__pycache__", ".git"}
    files = [fp for fp in (list(root.rglob("*.py")) + list(root.rglob("*.md")))
             if not any(excl in fp.parts for excl in EXCLUDED_DIRS)]

    for concept in concepts:
        appearances = []
        for fp in files:
            try:
                if concept in fp.read_text(encoding="utf-8"):
                    appearances.append(str(fp.relative_to(root)))
            except (UnicodeDecodeError, OSError):
                continue
        # Threshold: >6 files — below this is normal modular reference
        if len(appearances) > 6:
            findings.append({
                "pattern": "distributed-state",
                "concept": concept,
                "files": appearances[:5],
                "count": len(appearances),
                "description": f"'{concept}' 散落在 {len(appearances)} 个文件中——可能需要在单一位置集中管理"
            })

    return findings


def check_pattern_doc_code_drift(root: Path) -> list:
    """Pattern 4: Check for doc-code drift.

    Compares skill.md's described structure with actual files.
    """
    findings = []

    skill_md = None
    for name in ("skill.md", "SKILL.md"):
        p = root / name
        if p.exists():
            skill_md = p
            break

    if not skill_md:
        return [{"pattern": "missing-skill-md", "description": "skill.md 不存在"}]

    # Check if manifest references files that don't exist
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inventory = manifest.get("file_inventory", {})

        # Map manifest category keys to filesystem paths
        CATEGORY_PATH_MAP = {
            "engine": "engine",
            "engine_system1": "engine/system1",
            "engine_system2": "engine/system2",
            "skills": "skills",
            "references": "references",
            "data": "data",
        }

        for category, file_list in inventory.items():
            base_path = CATEGORY_PATH_MAP.get(category, category)
            for f in file_list:
                # Handle directory entries (end with /)
                if f.endswith("/"):
                    p = root / base_path / f.rstrip("/")
                    if not p.is_dir():
                        findings.append({
                            "pattern": "manifest-drift",
                            "file": f"{category}/{f}",
                            "description": f"manifest.json 声明了 {category}/{f} 但它不存在"
                        })
                else:
                    # Try direct path and subdirectories
                    found = False
                    for subdir in ["", "system1", "system2"]:
                        p = root / base_path / subdir / f
                        if p.exists():
                            found = True
                            break
                    # Also try category-as-directory directly
                    p = root / base_path / f
                    if not found and p.exists():
                        found = True
                    if not found and (root / f).exists():
                        found = True

                    if not found:
                        findings.append({
                            "pattern": "missing-declared-file",
                            "file": f"{category}/{f}",
                            "description": f"file_inventory 声明了 {category}/{f} 但找不到"
                        })

    return findings


def check_pattern_homoiconicity(root: Path) -> list:
    """Anchor A/D check: Is skill-builder-v3's own structure the same as any other skill?

    This is the core self-reference check. It verifies that skill-builder-v3
    can be treated identically to any other skill by the engine.
    """
    findings = []

    # Check 1: Does skill-builder-v3 have all the files it requires other skills to have?
    required_files = [
        "skill.md",
        "manifest.json",
        "references/known-issues.md",
        "references/patterns.md",
        "references/changelog-archive.json",
    ]

    for rf in required_files:
        # skill.md can be SKILL.md
        if rf == "skill.md":
            if not (root / "skill.md").exists() and not (root / "SKILL.md").exists():
                findings.append({"pattern": "homoiconicity-violation",
                                 "description": f"自身缺少 {rf}——其他 skill 被要求有"})
        elif not (root / rf).exists():
            findings.append({"pattern": "homoiconicity-violation",
                             "description": f"自身缺少 {rf}——其他 skill 被要求有"})

    # Check 2: engine/ code uses only <skill-name> as variable, never hardcodes
    # Exclude files whose job IS to check for self-reference
    SELF_CHECK_FILES = {"anchor.py", "patterns.py", "scanner.py", "realizability.py", "bootstrap.py", "trace_distiller.py", "redteam.py", "regression_guard.py"}
    engine_dir = root / "engine"
    if engine_dir.exists():
        for py_file in engine_dir.rglob("*.py"):
            if py_file.name in SELF_CHECK_FILES:
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""'):
                        continue
                    # Detect if there's a branch that special-cases skill-builder-v3
                    if ("skill-builder-v3" in stripped.lower() and
                        any(kw in stripped for kw in ["if ", "elif ", "== ", "!=", "is not"])):
                        if "default" not in stripped and "example" not in stripped:
                            findings.append({
                                "pattern": "self-special-path",
                                "file": str(py_file.relative_to(root)),
                                "line": stripped[:100],
                                "description": "检测到对 skill-builder-v3 的特殊路径分支——违反同构性"
                            })
            except (UnicodeDecodeError, OSError):
                continue

    return findings


# ── (HyperAgents) Cross-skill pattern bootstrap ───────────────────────────────

def check_cross_skill_patterns(skill_name: str) -> dict:
    """(HyperAgents) Apply global cross-skill insights to a specific skill.

    Reads global_insights and checks if any unapplied patterns apply
    to this skill. This enables meta-level transfer: patterns discovered
    in one skill are automatically checked against all others.
    """
    from engine import memory
    from engine.system1 import scanner

    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在", "applied": 0}

    global_insights = memory.read_global_insights(limit=50)
    unapplied = [g for g in global_insights
                 if g.get("application_count", 0) == 0
                 and skill_name in g.get("affected_skills", [])]

    findings = []
    for ins in unapplied:
        pattern_name = ins.get("pattern_name", "")
        if not pattern_name:
            continue

        # Check if this pattern exists in the current skill
        scan = scanner.scan_skill(skill_name)
        matching = [f for f in scan.get("findings", [])
                    if f.get("pattern") == pattern_name]

        if matching:
            findings.append({
                "pattern": f"global-insight:{pattern_name}",
                "severity": "warning",
                "description": f"全局洞察: {ins.get('text', '')[:200]}",
                "global_insight_id": ins.get("global_insight_id", ""),
                "source_skill": ins.get("source_skill", ""),
                "matching_files": [m.get("file", "?") for m in matching[:5]]
            })
            # Mark as applied
            memory.mark_global_insight_applied(ins["global_insight_id"])

    return {
        "skill": skill_name,
        "global_insights_checked": len(unapplied),
        "matching_patterns": len(findings),
        "findings": findings,
        "verdict": f"[OK] {len(findings)} 个跨技能模式匹配" if findings
                   else "[OK] 无适用的跨技能全局洞察"
    }


# ── Full pattern audit ────────────────────────────────────────────────────────

def audit_patterns(skill_name: str) -> dict:
    """Run all pattern checks against a skill."""
    root = _find_skill_root(skill_name)
    if not root:
        return {"error": f"技能 '{skill_name}' 不存在", "findings": []}

    all_findings = []
    all_findings.extend(check_pattern_distributed_state(root))
    all_findings.extend(check_pattern_doc_code_drift(root))
    all_findings.extend(check_pattern_homoiconicity(root))

    # (HyperAgents) Cross-skill pattern bootstrap
    cross = check_cross_skill_patterns(skill_name)
    all_findings.extend(cross.get("findings", []))

    # Also run the fast scanner
    from engine.system1 import scanner
    scan_result = scanner.scan_skill(skill_name)
    all_findings.extend(scan_result.get("findings", []))

    return {
        "skill": skill_name,
        "findings_count": len(all_findings),
        "findings": all_findings,
        "pattern_types": list(set(f.get("pattern", "unknown") for f in all_findings)),
        "cross_skill_matches": cross.get("matching_patterns", 0)
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="System 1 模式匹配——结构性反模式检测")
    parser.add_argument("skill",
                        help="目标 skill 名称")
    parser.add_argument("--check", choices=["distributed-state", "doc-drift", "homoiconicity", "all"],
                        default="all", help="运行指定检查")

    args = parser.parse_args()

    result = audit_patterns(args.skill)
    if "error" in result:
        print(f"[!!] {result['error']}")
        sys.exit(1)

    print(f"  模式审计: {args.skill}")
    print(f"  发现: {result['findings_count']}")
    print(f"  类型: {result['pattern_types']}")
    print()

    for f in result["findings"][:30]:
        p = f.get("pattern", "?")
        desc = f.get("description", "")[:120]
        print(f"  [{p}] {desc}")

    if result["findings_count"] == 0:
        print(f"  [OK] 无模式违规")


if __name__ == "__main__":
    main()
