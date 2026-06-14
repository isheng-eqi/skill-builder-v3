#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Deep Audit — 语义级代码审查。发现 scanner (regex) 永远检测不到的问题。

Scanner 只能做文本模式匹配。以下五类语义缺陷 scanner 完全看不见:
  1. PSEUDO_FIX — fix 函数只加注释不改变行为
  2. TAUTOLOGICAL_VERIFY — 验证测试的是"操作是否被执行"而非"问题是否被解决"
  3. HARDCODED_METRIC — 声称在测量的函数实际返回硬编码常量
  4. CODE_DUPLICATION — 同一函数内相同代码块重复多次 (copy-paste bug)
  5. PSEUDO_CAUSAL — 将时间相邻标记为因果关系

v4 架构: 本模块生成结构化审计简报。Claude 阅读引擎源码，
对每个类别独立判断，输出与 scanner 相同格式的 findings，
从而接入标准进化管道。

Usage:
  python engine/deep_audit.py <skill-name> --output audit_brief.json
    → 生成审计简报 JSON，包含引擎源码和审查问题

  python engine/deep_audit.py <skill-name> --record findings.json
    → 记录 Claude 审查后发现的语义缺陷

  将此步插入 v4 决策树: 每 N 轮运行一次 deep audit，
  Claude 发现的语义缺陷与 scanner findings 合并判断。
"""

import json
import re
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.platform import find_skill_root as _find_skill_root

# ── Audit categories ──────────────────────────────────────────────────────────

AUDIT_CATEGORIES = {
    "PSEUDO_FIX": {
        "id": "pseudo_fix",
        "title": "伪修复检测 — fix 函数只加注释不改变行为",
        "question": (
            "审查每个 fix 函数的源码。判断该函数是否真正改变了代码行为，"
            "还是仅仅添加了注释/TODO/标注。\n\n"
            "判定标准:\n"
            "- GENUINE: 修改了代码的控制流、数据流、或替换了实际代码\n"
            "- PSEUDO:  只添加了 '# TODO: ...', '# DEAD: ...', 或类似注释行\n"
            "- MIXED:   有些情况改行为，有些情况只加注释\n\n"
            "对于 PSEUDO_FIX，指出: 应该如何处理？(重写函数 / 降为detect-only / 废弃)"
        ),
        "severity": "critical"
    },
    "TAUTOLOGICAL_VERIFY": {
        "id": "tautological_verify",
        "title": "同义反复验证 — 验证检查的是操作是否被执行，而非问题是否被解决",
        "question": (
            "审查每个验证函数和 _build_verification_commands。\n"
            "判断验证是否独立于修复方法:\n\n"
            "- 如果修复操作是 '加了一行 # TODO: xxx'，验证命令是 'grep TODO xxx' → 同义反复\n"
            "- 如果修复操作是 '加了一行 # TODO: xxx'，验证命令是 'compile()' → 同义反复(注释不破坏编译)\n"
            "- 如果修复操作是 'grep -P → grep -E'，验证命令是 'grep -c ^# skill.md' → 无关联\n\n"
            "真正的验证: 测试修复是否解决了原始问题，而非测试修复操作是否被应用。"
        ),
        "severity": "critical"
    },
    "HARDCODED_METRIC": {
        "id": "hardcoded_metric",
        "title": "硬编码指标 — 声称在测量的函数实际返回硬编码常量",
        "question": (
            "审查所有返回数值评分的函数 (fitness, snr, quality, score 等)。\n"
            "判断返回值是否由实际数据计算得出:\n\n"
            "- 如果函数体中有类似 'if param == x: return 0.1; elif param == y: return -0.05'"
            " 的硬编码映射表，且从未实际运行测量 → HARDCODED\n"
            "- 如果函数从 event log / metrics / 实际执行中读取数据并计算 → GENUINE\n"
            "- 如果函数注释说 'In production this would be measured empirically' → 诚实声明, "
            "但仍然是 HARDCODED\n\n"
            "危害: 假爬山 (hillclimb) 用这些硬编码值做优化决策，优化的是幻象。"
        ),
        "severity": "critical"
    },
    "CODE_DUPLICATION": {
        "id": "code_duplication",
        "title": "代码重复 — 同一函数内完全相同的代码块出现多次",
        "question": (
            "审查所有引擎 Python 文件。寻找同一函数内重复出现 3+ 次的完全相同的代码块"
            " (至少 3 行完全相同)。\n\n"
            "典型信号:\n"
            "- 同一个 try/except 块被复制粘贴多次\n"
            "- 同一个 compile() 调用连续出现多次\n"
            "- 同一个条件判断 + 返回逻辑重复出现\n\n"
            "对于每个发现，指出: 重复了几次、在哪个函数中、建议如何消除重复。"
        ),
        "severity": "warning"
    },
    "PSEUDO_CAUSAL": {
        "id": "pseudo_causal",
        "title": "伪因果 — 将时间相邻错误标记为因果关系",
        "question": (
            "审查所有建立'因果'链接的代码 (record_descendant_link, relation='enabled' 等)。\n"
            "判断因果关系是否真实:\n\n"
            "- 如果 A 修复确实为 B 修复创造了条件 (如 A 修复了导入, B 才能运行) → 真因果\n"
            "- 如果仅因为 A 和 B 在同一轮被执行就标记为 'enabled' → 伪因果\n"
            "- 如果没有任何排除混淆变量的逻辑 → 伪因果\n\n"
            "危害: HGM 元生产力评分基于假因果链，高分规则可能只是运气好。"
        ),
        "severity": "warning"
    }
}


# ── Source code collection ─────────────────────────────────────────────────────

def collect_engine_sources(skill_name: str) -> dict:
    """Collect all engine Python source files for audit.

    Returns {relative_path: source_code} for all .py files under engine/.
    Excludes __pycache__, compiled .pyc, and this module itself.
    """
    root = _find_skill_root(skill_name)
    if not root:
        return {}

    engine_dir = root / "engine"
    self_path = Path(__file__).resolve()
    sources = {}
    for py_file in sorted(engine_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        # v4 fix: exclude self — the auditor should not audit itself.
        # Self-audit produces false positives on heuristic detection code.
        if py_file.resolve() == self_path:
            continue
        rel = str(py_file.relative_to(root))
        try:
            sources[rel] = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            sources[rel] = f"# (binary or unreadable: {py_file})"
    return sources


def _audit_data_dir(skill_name: str) -> Optional[Path]:
    root = _find_skill_root(skill_name)
    if not root:
        return None
    d = root / "data" / "deep_audits"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Brief generation ───────────────────────────────────────────────────────────

def generate_audit_brief(skill_name: str) -> dict:
    """Generate a structured audit brief for Claude.

    The brief contains:
    1. All engine source code (so Claude can read it)
    2. For each category, specific questions and detection heuristics
    3. Expected output format

    Claude reads this brief, reviews the code, and fills in findings.
    """
    sources = collect_engine_sources(skill_name)
    root = _find_skill_root(skill_name)

    # Detect which categories are most relevant based on code content
    relevance = _assess_category_relevance(sources)

    brief = {
        "meta": {
            "skill": skill_name,
            "audit_type": "deep_semantic",
            "iso_generated": datetime.now(timezone.utc).isoformat(),
            "total_files": len(sources),
            "total_lines": sum(len(s.split("\n")) for s in sources.values()),
            "instruction": (
                "Claude: 这是深层语义审计。Scanner (regex) 无法检测以下五类问题。"
                "你需要阅读引擎源码，对每个类别独立判断，输出结构化 findings。"
                "每个 finding 的格式与 scanner 输出相同，可以直接接入进化管道。"
            )
        },
        "categories": {},
        "engine_sources": {},
        "output_schema": {
            "findings": [
                {
                    "pattern": "deep-audit:<category_id>",
                    "severity": "critical|warning|info",
                    "file": "engine/xxx.py",
                    "line": 42,
                    "description": "人类可读的问题描述",
                    "match": "触发问题的代码片段",
                    "deep_audit": {
                        "category": "PSEUDO_FIX",
                        "confidence": 0.9,
                        "recommendation": "建议的修复方向"
                    }
                }
            ]
        }
    }

    # Include only the most relevant files for each category to keep brief manageable
    for cat_id, cat_info in AUDIT_CATEGORIES.items():
        relevant_files = _select_relevant_files(cat_id, sources, relevance)
        brief["categories"][cat_id] = {
            "title": cat_info["title"],
            "question": cat_info["question"],
            "severity": cat_info["severity"],
            "relevant_files": list(relevant_files.keys()),
            "files_to_review": relevant_files,
            "detection_heuristics": _detection_heuristics(cat_id)
        }

    # Include all engine sources (Claude needs to see the code)
    brief["engine_sources"] = sources

    return brief


def _assess_category_relevance(sources: dict) -> dict:
    """Quick pre-scan to determine which categories are most relevant."""
    relevance = {cat: 0.0 for cat in AUDIT_CATEGORIES}

    for path, code in sources.items():
        # PSEUDO_FIX: look for fix functions that add comments
        if "fix_" in path.lower() or "rules.py" in path:
            if "# TODO" in code and "def fix_" in code:
                relevance["PSEUDO_FIX"] += 1.0

        # TAUTOLOGICAL_VERIFY: look for verify functions
        if "verify" in path.lower() or "loop.py" in path:
            if "grep" in code and "fix" in code.lower():
                relevance["TAUTOLOGICAL_VERIFY"] += 0.5

        # HARDCODED_METRIC: look for functions returning hardcoded values
        if any(kw in path.lower() for kw in ["hillclimb", "fitness", "metric", "score"]):
            relevance["HARDCODED_METRIC"] += 1.0
        if "return" in code and ("0.1" in code or "0.05" in code or "-0.1" in code):
            if "def " in code:
                relevance["HARDCODED_METRIC"] += 0.2

        # CODE_DUPLICATION: all files are candidates
        relevance["CODE_DUPLICATION"] += 0.1

        # PSEUDO_CAUSAL: look for causal link code
        if "descendant" in code.lower() or "causal" in code.lower():
            relevance["PSEUDO_CAUSAL"] += 1.0

    return relevance


def _select_relevant_files(cat_id: str, sources: dict, relevance: dict) -> dict:
    """Select files most relevant to a category."""
    # Map categories to file patterns
    patterns = {
        "PSEUDO_FIX": ["rules.py", "code_generator.py", "rule_generator.py"],
        "TAUTOLOGICAL_VERIFY": ["loop.py", "evidence.py"],
        "HARDCODED_METRIC": ["hillclimb.py", "memory.py", "loop.py"],
        "CODE_DUPLICATION": ["rules.py", "evidence.py", "loop.py", "memory.py",
                            "reflect.py", "anchor.py"],
        "PSEUDO_CAUSAL": ["memory.py", "loop.py"],
    }

    target_files = patterns.get(cat_id, [])
    selected = {}
    for rel_path, code in sources.items():
        filename = Path(rel_path).name
        # Include if filename matches OR path contains relevant keywords
        if filename in target_files or any(t in rel_path for t in target_files):
            selected[rel_path] = code
    return selected


def _detection_heuristics(cat_id: str) -> list:
    """Generate specific detection heuristics for a category."""
    heuristics = {
        "PSEUDO_FIX": [
            "在 fix 函数源码中搜索 'TODO' 或 '标注' 或 'annotated'",
            "如果函数的核心操作是字符串拼接注释 (如 x + '  # TODO') → PSEUDO",
            "如果函数的核心操作是替换/插入/删除实际代码 → GENUINE",
            "检查 fix 函数的 return dict: action 字段是否包含 '标注' 或 'TODO'"
        ],
        "TAUTOLOGICAL_VERIFY": [
            "在 _build_verification_commands 中找 grep 命令",
            "如果 grep 搜索的内容恰好是修复操作写入的内容 → 同义反复",
            "如果验证命令是 compile() 而修复只是加了注释 → 同义反复 (注释不破坏编译)",
            "真正的验证: 独立于修复方法的测试"
        ],
        "HARDCODED_METRIC": [
            "在 hillclimb.py 中搜索 _approximate_fitness_delta",
            "检查是否存在 if-elif 链, 每个分支返回固定数值",
            "检查函数注释中是否有 'In production this would be measured'",
            "任何声称'测量'但实际返回常量的函数都是硬编码指标"
        ],
        "CODE_DUPLICATION": [
            "逐函数检查: 取连续的 3+ 行代码, 在该函数内搜索是否出现多次",
            "特别注意 try/except 块和 compile() 调用的重复",
            "即使变量名略有不同, 只要结构相同就算重复"
        ],
        "PSEUDO_CAUSAL": [
            "在 memory.py:record_descendant_link 检查因果关系建立逻辑",
            "检查 loop.py 中调用 record_descendant_link 的上下文",
            "如果 A 和 B 仅因在同一轮被执行而建立链接 → 伪因果",
            "真因果需要: A 改变的状态被 B 读取和使用"
        ]
    }
    return heuristics.get(cat_id, [])


# ── Recording findings ────────────────────────────────────────────────────────

def record_deep_audit_findings(skill_name: str, findings: list) -> dict:
    """Record deep audit findings into the event log.

    Findings are in the same format as scanner findings, so they
    integrate seamlessly into the evolution pipeline.
    """
    from engine import memory

    ad = _audit_data_dir(skill_name)
    if not ad:
        return {"error": "Skill not found"}

    ts = datetime.now(timezone.utc)
    audit_record = {
        "audit_id": f"deep-{ts.strftime('%Y%m%d-%H%M%S')}",
        "iso_audited": ts.isoformat(),
        "skill": skill_name,
        "categories_checked": list(set(
            f.get("deep_audit", {}).get("category", "?")
            for f in findings
        )),
        "findings_count": len(findings),
        "findings": findings
    }

    # Save audit record
    fp = ad / f"{audit_record['audit_id']}.json"
    fp.write_text(json.dumps(audit_record, indent=2, ensure_ascii=False),
                  encoding="utf-8")

    # Write each finding as an event
    for finding in findings:
        category = finding.get("deep_audit", {}).get("category", "?")
        memory.write_event(skill_name, "insight", {
            "source": "deep_audit",
            "audit_id": audit_record["audit_id"],
            "category": category,
            "pattern": finding.get("pattern", "?"),
            "file": finding.get("file", "?"),
            "line": finding.get("line", 0),
            "description": finding.get("description", "")[:500],
            "recommendation": finding.get("deep_audit", {}).get("recommendation", ""),
            "confidence": finding.get("deep_audit", {}).get("confidence", 0.5)
        })

    # Write insight if critical issues found
    critical = [f for f in findings
                if f.get("severity") == "critical"]
    if critical:
        memory.write_insight(
            skill_name,
            f"深层语义审计发现 {len(critical)} 个严重问题: "
            + "; ".join(
                f"{f.get('deep_audit', {}).get('category', '?')}: "
                f"{f.get('description', '')[:100]}"
                for f in critical[:5]
            ),
            [],
            confidence=0.85
        )

    return {
        "audit_id": audit_record["audit_id"],
        "recorded": len(findings),
        "critical": len(critical),
        "verdict": (
            f"[OK] {len(findings)} 个语义缺陷已记录"
            if findings
            else "[OK] 深层审计未发现语义缺陷"
        )
    }


def read_deep_audits(skill_name: str, limit: int = 10) -> list:
    """Read recent deep audit records."""
    ad = _audit_data_dir(skill_name)
    if not ad:
        return []
    records = []
    for fp in sorted(ad.glob("deep-*.json"), reverse=True):
        try:
            records.append(json.loads(fp.read_text(encoding="utf-8")))
            if len(records) >= limit:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return records


# ── Auto-trigger detection ────────────────────────────────────────────────────

def should_run_deep_audit(skill_name: str, min_rounds: int = 5,
                           min_changes: int = 3) -> dict:
    """Determine if a deep audit should run.

    Triggers when:
    - No deep audit has ever run (first time)
    - Last audit is older than min_rounds
    - Engine files have changed since last audit

    Returns {should_run, reason, last_audit_iso, rounds_since}.
    """
    from engine import memory

    audits = read_deep_audits(skill_name, limit=1)
    if not audits:
        return {"should_run": True, "reason": "从未运行过深层语义审计",
                "last_audit_iso": None, "rounds_since": -1}

    last_audit = audits[0]
    last_iso = last_audit.get("iso_audited", "")

    # Count loop events since last audit
    events = memory.read_events(skill_name, limit=200)
    rounds_since = 0
    for e in events:
        if e.get("iso_timestamp", "") <= last_iso:
            break
        if e.get("event_type") == "loop":
            rounds_since += 1

    if rounds_since >= min_rounds:
        return {"should_run": True,
                "reason": f"距上次审计已过 {rounds_since} 轮",
                "last_audit_iso": last_iso,
                "rounds_since": rounds_since}

    # Check if engine files changed since last audit
    fix_events_since = [
        e for e in events
        if e.get("event_type") == "fix"
        and e.get("iso_timestamp", "") > last_iso
        and e.get("data", {}).get("phase") != "descendant_link"
    ]
    if len(fix_events_since) >= min_changes:
        return {"should_run": True,
                "reason": f"引擎文件在上次审计后有 {len(fix_events_since)} 处修改",
                "last_audit_iso": last_iso,
                "rounds_since": rounds_since}

    return {"should_run": False,
            "reason": f"[OK] 审计有效 ({rounds_since} 轮前)",
            "last_audit_iso": last_iso,
            "rounds_since": rounds_since}


# ── Quick semantic pre-scan ───────────────────────────────────────────────────

def quick_semantic_scan(skill_name: str) -> dict:
    """Fast pre-scan for obvious semantic issues without full audit.

    Uses simple heuristics to flag files that likely contain semantic defects.
    This runs automatically (no Claude needed) and tells Claude which
    files to focus on in the full audit.

    Returns {suspicious_files: {path: [reasons]}}.
    """
    sources = collect_engine_sources(skill_name)
    suspicious = {}

    for rel_path, code in sources.items():
        reasons = []

        # Heuristic 1: fix function that only adds comments
        if "def fix_" in code:
            # Count lines that actually modify code vs add comments
            lines = code.split("\n")
            in_fix = False
            todo_lines = 0
            code_lines = 0
            for line in lines:
                if "def fix_" in line:
                    in_fix = True
                    continue
                if in_fix and line.strip().startswith("def "):
                    break
                if in_fix:
                    if "# TODO" in line or "# DEAD" in line:
                        todo_lines += 1
                    elif any(kw in line for kw in ["write_text", "replace(", "re.sub",
                                                     "insert(", "append("]):
                        code_lines += 1
            if todo_lines > 0 and code_lines == 0:
                reasons.append(f"PSEUDO_FIX_SUSPECT: function adds TODO comments, no code modification")

        # Heuristic 2: hardcoded return values in measurement functions
        if any(kw in rel_path.lower() for kw in ["fitness", "hillclimb", "metric"]):
            if "return -0.1" in code or "return 0.05" in code or "return -0.05" in code:
                # These are specific magic numbers from _approximate_fitness_delta
                if "def _approximate" in code or "def _approx" in code:
                    reasons.append("HARDCODED_METRIC_SUSPECT: returns fixed constants without measurement")

        # Heuristic 3: duplicated code blocks (only in functions, skip data structures)
        # v4 fix: exclude common boilerplate lines that appear in every fix function.
        # These are NOT copy-paste bugs — they're the standard fix function template.
        BOILERPLATE_PREFIXES = (
            "fp.write_text(", "fp.read_text(", "fp = root / finding",
            "return {\"applied\":", "if not fp.exists():", "content = fp.read_text",
            "lines = content.split", "line_num = finding.get",
            "new_content = ", "import json", "import re",
            "try:", "except ", "finally:", "else:",
        )
        if "def fix_" in code:
            # Collect lines that are inside a function (indented code), not top-level data
            func_lines = []
            for l in code.split("\n"):
                stripped = l.strip()
                # Skip comments, empty lines, dict entries, string-only lines
                if not stripped or stripped.startswith("#") or stripped.startswith('"""'):
                    continue
                if stripped.startswith('"') or stripped.startswith("'"):
                    continue
                if stripped.startswith("{") or stripped.startswith("}"):
                    continue
                if len(stripped) > 30:
                    func_lines.append(stripped)
            # v4: exclude boilerplate before counting duplicates
            func_lines = [l for l in func_lines
                          if not any(l.startswith(p) for p in BOILERPLATE_PREFIXES)]
            for line in func_lines:
                count = func_lines.count(line)
                if count >= 3:
                    reasons.append(f"CODE_DUP_SUSPECT: identical line repeated {count}x in file")
                    break

        # Heuristic 4: descendant link without causality check (across lines, not same line)
        if "record_descendant_link" in code:
            dl_lines = code.split("\n")
            for j, dl in enumerate(dl_lines):
                st = dl.strip()
                if st.startswith("#") or st.startswith('"""'):
                    continue
                nxt = dl_lines[j+1].strip() if j+1 < len(dl_lines) else ""
                if "enumerate" in st and "for" in st and (
                    "child" in st or "child" in nxt or "i+1" in nxt or "new_fixes" in nxt
                ):
                    nearby = "\n".join(dl_lines[max(0,j-2):min(len(dl_lines),j+10)])
                    if "record_descendant_link" in nearby:
                        reasons.append("PSEUDO_CAUSAL_SUSPECT: adjacent fixes linked as causal without independence check")
                        break

        # Heuristic 5: verify checks for fix artifact, not problem solved
        # Catch: grep for TODO / grep for expected_tag / grep TAG_OK / compile-only verify
        for j, dl in enumerate(code.split("\n")):
            st = dl.strip()
            if st.startswith("#"):
                continue
            if "grep" in st and ("TODO" in st or "expected_tag" in st or "TAG_OK" in st):
                reasons.append("TAUTOLOGICAL_VERIFY_SUSPECT: verification checks fix artifact (grep for tag), not whether problem solved")
                break

        # Heuristic 6: PSEUDO_FIX variant — fix returns "annotated"/"标注" (comment-only fix)
        # v4 fix: skip docstring lines. "标注" in a docstring describes what the TOOL detects,
        # not what the code does.
        if "def fix_" in code:
            in_triple6 = False
            for j, dl in enumerate(code.split("\n")):
                st = dl.strip()
                if st.startswith('"""') or st.startswith("'''"):
                    in_triple6 = not in_triple6
                    continue
                if in_triple6 or st.startswith("#"):
                    continue
                if ("annotated" in st.lower() or "标注" in st):
                    if "return" in st.lower() or "action" in st or "reason" in st:
                        reasons.append("PSEUDO_FIX_SUSPECT: fix function return value contains 'annotated'/标注 — fix only adds comments, not behavior change")
                        break

        # Heuristic 7: UNREACHABLE_INTELLIGENCE — builds LLM prompt but never calls LLM
        # v4 fix: skip docstring lines and template strings. The word "prompt_ready"
        # in a docstring is a specification, not actual code returning it.
        in_triple_quote = False
        for j, dl in enumerate(code.split("\n")):
            st = dl.strip()
            if st.startswith('"""') or st.startswith("'''"):
                in_triple_quote = not in_triple_quote
                continue
            if in_triple_quote or st.startswith("#"):
                continue
            if '"prompt_ready"' in st and "status" in st.lower():
                fn_area = "\n".join(code.split("\n")[max(0,j-25):min(len(code.split("\n")),j+3)])
                if "build" in fn_area.lower() and "prompt" in fn_area.lower():
                    reasons.append("UNREACHABLE_INTELLIGENCE_SUSPECT: builds LLM prompt, returns 'prompt_ready', but no caller sends it to LLM")
                    break

        # Heuristic 8: SILENT_IMPORT_FAILURES — excessive except ImportError: pass
        dl_lines = code.split("\n")
        ip_count = sum(1 for j, dl in enumerate(dl_lines)
                       if "except ImportError" in dl.strip()
                       and "pass" in dl_lines[min(j+1, len(dl_lines)-1)].strip())
        if ip_count >= 10:
            reasons.append(f"SILENT_IMPORT_FAILURES_SUSPECT: approximately {ip_count} silent 'except ImportError: pass' — module failures invisible")

        # Heuristic 9: SAME_IMPORT_COPIED — identical import copied across many files
        # "find_skill_root imported 17x" violates Pattern 4 (distributed implementation)
        import_patterns = {}
        for j, dl in enumerate(code.split("\n")):
            s = dl.strip()
            if s.startswith("from engine.") and "import" in s:
                key = s.split("import")[-1].strip()
                import_patterns[key] = import_patterns.get(key, 0) + 1
        # This heuristic fires only in the cross-file check below (single file won't detect distribution)

        # Heuristic 10: SELF_AWARE_DEAD_CODE — a function that claims to auto-X but is never called
        # Pattern: def install_* / def auto_* / def bootstrap_* but no main() or __name__ calls it
        if "def install_" in code or "def auto_install" in code:
            func_name = ""
            for j, dl in enumerate(code.split("\n")):
                s = dl.strip()
                if "def install_" in s or "def auto_install" in s:
                    func_name = s.split("(")[0].replace("def ", "")
                    break
            if func_name and func_name + "(" not in code.split("def " + func_name)[-1]:
                # The function is defined but never called within its own file
                pass  # Can't detect cross-file without multi-file analysis

        # Heuristic 11: MONKEY_PATCH — fragile test patching internals
        # v4 fix: skip docstring lines and heuristic description lines. "monkey" in a
        # docstring/comment describes what the TOOL detects, not actual monkey-patching.
        if "monkey" in code.lower() or "._find_skill_root = " in code or "patched_" in code:
            if "def run_" in code or "def test_" in code or "def setup_" in code:
                in_triple11 = False
                for j, dl in enumerate(code.split("\n")):
                    st = dl.strip()
                    if st.startswith('"""') or st.startswith("'''"):
                        in_triple11 = not in_triple11
                        continue
                    if in_triple11 or st.startswith("#"):
                        continue
                    if "._find_skill_root" in dl or "monkey" in dl.lower():
                        reasons.append("MONKEY_PATCH_SUSPECT: test monkey-patches internal function — fragile, breaks on refactor")
                        break

        # Heuristic 12: SANDBOX_ONLY_TEST — test runs against temp sandbox, never real engine
        if ("tempfile.mkdtemp" in code or "mkdtemp" in code) and "sandbox" in code.lower():
            if "def run_" in code or "def setup_" in code:
                # Check: after creating sandbox, is there any test against the REAL engine path?
                has_real_test = any(
                    "SKILL_ROOT" in dl or "skill_root" in dl or str(SKILL_ROOT) in dl
                    for dl in code.split("\n")
                    if not dl.strip().startswith("#")
                )
                if not has_real_test:
                    reasons.append("SANDBOX_ONLY_SUSPECT: test creates sandbox but never validates against real engine — may miss real regressions")

        if reasons:
            suspicious[rel_path] = reasons

    return {
        "skill": skill_name,
        "files_scanned": len(sources),
        "suspicious_files": len(suspicious),
        "suspicious": suspicious,
        "verdict": (
            f"[!!] {len(suspicious)} 个文件存在疑似语义缺陷——建议运行完整 deep audit"
            if suspicious
            else "[OK] 预扫描未发现明显语义问题"
        )
    }


# ── Guided audit: complete, actionable self-audit plan for Claude ────────────

def generate_guided_audit(skill_name: str) -> dict:
    """Generate a step-by-step audit plan Claude can execute autonomously.

    Runs prescan, then for each suspicious file, collects the relevant code
    snippets and generates focused questions. Claude reads each snippet,
    answers the question, builds finding dicts, and records them.
    """
    scan = quick_semantic_scan(skill_name)
    items = []

    # ── Cross-file heuristic: detect same import copied across many files ──
    sources = collect_engine_sources(skill_name)
    import_counts = {}
    for rel_path, code in sources.items():
        for line in code.split("\n"):
            s = line.strip()
            if s.startswith("from engine.") and "import" in s:
                key = s.split("import")[-1].strip().split(" as ")[0].strip()
                import_counts.setdefault(key, []).append(rel_path)
    for imp_name, files in import_counts.items():
        if len(files) >= 8:
            # Add as a top-level suspicious entry with a synthetic file path
            first_file = files[0]
            example_files = ", ".join(files[:5])
            scan.setdefault("suspicious", {}).setdefault(
                first_file, []
            ).append(
                f"DISTRIBUTED_STATE_SUSPECT: '{imp_name}' copied across {len(files)} files "
                f"({example_files}...) — violates Pattern 4. Consolidate to one canonical source."
            )

    # ── Cross-file dead code: functions defined but only called from their own CLI ──
    # Check hooks_bridge.install_hooks — is it called from loop/bootstrap/reflect?
    dead_candidates = {
        "install_hooks": "hooks_bridge.py",
    }
    for func_name, source_file in dead_candidates.items():
        called_elsewhere = False
        for rel_path, code in sources.items():
            if source_file in rel_path:
                continue  # Skip self-reference
            if func_name + "(" in code:
                called_elsewhere = True
                break
        if not called_elsewhere:
            scan.setdefault("suspicious", {}).setdefault(
                f"engine/{source_file}", []
            ).append(
                f"DEAD_CODE_SUSPECT: {func_name}() defined in {source_file} but never called "
                f"programmatically from any other engine module — only via CLI"
            )

    # ── Cross-file dead modules: files with __main__ but never imported by loop.py ──
    loop_code = sources.get("engine/loop.py", sources.get("engine\\\\loop.py", ""))
    if not loop_code:
        # Try Windows path
        for k in sources:
            if "loop.py" in k:
                loop_code = sources[k]
                break
    if loop_code:
        for rel_path, code in sources.items():
            fname = Path(rel_path).stem
            if fname in ("loop", "memory", "anchor", "reflect", "platform",
                         "scanner", "rules", "patterns", "context", "__init__"):
                continue
            if "__name__" in code and "main()" in code:
                # This file has a CLI — is it imported by loop.py?
                import_patterns = [
                    f"from engine import {fname}",
                    f"from engine.{fname} import",
                    f"import engine.{fname}",
                ]
                imported_by_loop = any(p in loop_code for p in import_patterns)
                if not imported_by_loop:
                    # Check if imported by any other module
                    imported_elsewhere = False
                    for other_path, other_code in sources.items():
                        if other_path == rel_path:
                            continue
                        if any(p in other_code for p in import_patterns):
                            imported_elsewhere = True
                            break
                    if not imported_elsewhere:
                        scan.setdefault("suspicious", {}).setdefault(
                            rel_path, []
                        ).append(
                            f"DEAD_MODULE_SUSPECT: {fname}.py has CLI but is never imported "
                            f"by loop.py or any other engine module — complete dead code"
                        )

    # ── Data growth check: excessive log/snapshot files ──
    root = _find_skill_root(skill_name)
    if root:
        log_count = len(list((root / "data" / "logs").glob("*.json"))) if (root / "data" / "logs").is_dir() else 0
        snap_count = len(list((root / "data" / "snapshots").glob("snap-*"))) if (root / "data" / "snapshots").is_dir() else 0
        if log_count > 500:
            scan.setdefault("suspicious", {}).setdefault(
                "data/logs/", []
            ).append(
                f"DATA_GROWTH_SUSPECT: {log_count} log files with no TTL/pruning policy — "
                f"append-only growth will eventually fill disk"
            )
        if snap_count > 50:
            scan.setdefault("suspicious", {}).setdefault(
                "data/snapshots/", []
            ).append(
                f"DATA_GROWTH_SUSPECT: {snap_count} snapshots with no retention limit"
            )

    # For each suspicious file, extract code context
    for rel_path, reasons in scan.get("suspicious", {}).items():
        code = sources.get(rel_path, "")
        if not code:
            continue
        lines_list = code.split("\n")
        for reason in reasons:
            # Determine category and generate question from reason text
            cat, q = _classify_reason(reason)
            line_num = _find_relevant_line(lines_list, reason)
            ctx_start = max(0, line_num - 6)
            ctx_end = min(len(lines_list), line_num + 8)
            snippet = "\n".join(f"  {line_num - 6 + i + 1:4d}| {l}"
                              for i, l in enumerate(lines_list[ctx_start:ctx_end]))
            items.append({
                "category": cat,
                "file": rel_path,
                "line": line_num + 1,
                "snippet": snippet,
                "reason": reason,
                "question": q
            })

    if not items:
        return {
            "skill": skill_name,
            "verdict": "[OK] No semantic defects found",
            "total_items": 0, "items": [],
            "next_step": "continue evolution loop"
        }

    by_cat = {}
    for item in items:
        by_cat.setdefault(item["category"], []).append(item)

    return {
        "skill": skill_name,
        "total_items": len(items),
        "categories_found": list(by_cat.keys()),
        "items_by_category": by_cat,
        "all_items": items,
        "instruction": (
            "CLAUDE: This is your self-audit plan. Review each item.\n\n"
            "For each item:\n"
            '  1. Read the code snippet (with line numbers)\n'
            '  2. Answer the question\n'
            '  3. Build a finding dict:\n'
            '     {"pattern":"deep-audit:<category>","severity":"critical|warning",\n'
            '      "file":"<path>","line":<N>,"description":"...",\n'
            '      "deep_audit":{"category":"<CAT>","confidence":0.X,\n'
            '                    "recommendation":"..."}}\n\n'
            "After reviewing ALL items, save to a JSON file and run:\n"
            "  python engine/deep_audit.py <skill> record --findings <file>\n\n"
            "Then apply standard evolution for each genuine finding."
        )
    }


def _classify_reason(reason: str) -> tuple:
    """Classify a prescan reason into audit category + generate question."""
    r = reason.lower()
    if "pseudo_fix" in r or "adds todo" in r or "comment" in r:
        return ("PSEUDO_FIX",
                "Read the fix function above. Does it genuinely change code behavior, "
                "or only add TODO comments? If pseudo-fix: rewrite function / demote to "
                "detect-only / deprecate?")
    if "hardcoded" in r or "constant" in r:
        return ("HARDCODED_METRIC",
                "Read this function. Are return values computed from actual data, "
                "or are they hardcoded constants? If doc says 'would be measured in "
                "production' -- honest but still hardcoded. Hillclimb optimizes on illusions.")
    if "code_dup" in r or "repeated" in r:
        return ("CODE_DUPLICATION",
                "Read around the highlighted line. Same code block repeated here. "
                "Copy-paste bug? How to eliminate the duplication?")
    if "pseudo_causal" in r or "adjacent" in r or "causal" in r:
        return ("PSEUDO_CAUSAL",
                "Read this code. Is there a real causal relationship between parent "
                "and child fixes, or are they linked just because they ran in the same round?")
    if "tautological" in r or "grep" in r or "todo" in r:
        return ("TAUTOLOGICAL_VERIFY",
                "Read this verification command. Does it test whether the PROBLEM was "
                "solved, or whether the FIX OPERATION was applied? If fix adds TODO "
                "and verify greps for TODO -- this is tautology.")
    if "unreachable" in r or "prompt_ready" in r:
        return ("PSEUDO_FIX",
                "This function builds an LLM prompt but returns 'prompt_ready' without "
                "ever sending it to an LLM. The intelligence layer (System 2) is "
                "unreachable — it exists on paper but never runs. "
                "Fix: v4 Claude should read deliberation prompts and generate responses.")
    if "silent_import" in r or "importerror" in r.lower():
        return ("PSEUDO_FIX",
                "This file has excessive 'except ImportError: pass' — module failures "
                "are completely silent. If a module is renamed or moved, functionality "
                "disappears with no error. Replace with logging.warning or "
                "conditional import checks.")
    if "monkey_patch" in r or "monkey" in r:
        return ("TAUTOLOGICAL_VERIFY",
                "This test monkey-patches an internal function. If the function "
                "is renamed or its signature changes, the test silently breaks — "
                "producing false positives/negatives. Use proper dependency injection.")
    if "distributed_state" in r or "distributed" in r:
        return ("PSEUDO_FIX",
                "This import is copy-pasted across many files. The system's own "
                "Pattern 4 says: '每个概念只在一个地方实现，其他地方通过调用引用它'. "
                "Consolidate into one canonical import, others re-import from there.")
    if "dead_module" in r or "dead_code" in r:
        return ("PSEUDO_FIX",
                "This module has a CLI entry point but is never imported or called "
                "by the evolution loop. Its intelligence is never applied. "
                "Either integrate it into loop.py or document why it's standalone.")
    if "data_growth" in r or "growth" in r:
        return ("TAUTOLOGICAL_VERIFY",
                "Event logs and snapshots grow without bound — no TTL, pruning, "
                "or retention policy. Append-only storage will eventually fill disk. "
                "Add log rotation and snapshot retention limits.")
    return ("UNKNOWN",
            f"Review this code for semantic defects: {reason[:200]}")


def _find_relevant_line(lines_list: list, reason: str) -> int:
    """Find the most relevant line number for a given reason."""
    r = reason.lower()
    for i, l in enumerate(lines_list):
        s = l.strip()
        if s.startswith("#"):
            continue
        if "pseudo_fix" in r and ("# TODO" in s or "# DEAD" in s):
            return i
        if "hardcoded" in r and ("return -0." in s or "return 0.0" in s):
            return i
        if "code_dup" in r and "compile(" in s:
            return i
        if "pseudo_causal" in r and "for i, parent" in s:
            return i
        if "tautological" in r and 'grep -qF "TODO' in s:
            return i
    # Fallback: find the first non-comment line
    for i, l in enumerate(lines_list):
        if l.strip() and not l.strip().startswith("#"):
            return i
    return 0


def print_guided_audit(audit_plan: dict):
    """Print the guided audit plan in Claude-readable format."""
    print(f"\n{'='*60}")
    print(f"  DEEP AUDIT GUIDE — {audit_plan['skill']}")
    print(f"  {audit_plan['total_items']} items in {len(audit_plan.get('categories_found',[]))} categories")
    print(f"  Instruction: {audit_plan.get('instruction','')[:200]}...")
    print(f"{'='*60}")

    for cat, items in audit_plan.get("items_by_category", {}).items():
        cat_sev = AUDIT_CATEGORIES.get(cat, {}).get("severity", "warning")
        print(f"\n{'─'*50}")
        print(f"  [{cat_sev.upper()}] {cat}: {len(items)} items")
        print(f"{'─'*50}")
        for idx, item in enumerate(items):
            print(f"\n  [{idx+1}] {item['file']}:{item['line']}")
            print(f"  REASON: {item['reason']}")
            print(f"  QUESTION: {item['question']}")
            print(f"  CODE:")
            print(f"{item['snippet']}")
            print(f"  FINDING TEMPLATE:")
            print(f'    pattern: deep-audit:{item["category"].lower()}')
            print(f'    file: {item["file"]}')
            print(f'    line: {item["line"]}')
            print(f'    description: <your finding>')
            print(f'    deep_audit: {{category: {item["category"]}, confidence: 0.X, recommendation: "..."}}')

    print(f"\n{'='*60}")
    print(f"  AFTER REVIEWING ALL {audit_plan['total_items']} ITEMS:")
    print(f"  1. Save all findings to a JSON file")
    print(f"  2. python engine/deep_audit.py {audit_plan['skill']} record --findings <file>")
    print(f"  3. Apply standard evolution for genuine fixes")
    print(f"{'='*60}\n")


# ── Integration: merge deep findings with scanner findings ─────────────────────

def merge_deep_findings(skill_name: str, scanner_findings: list) -> list:
    """Merge the latest deep audit findings into the scanner findings list.

    This creates a unified finding list that includes both:
    - Surface issues found by regex scanner
    - Semantic issues found by deep audit (Claude's last review)

    Used in the v4 decision tree Step 1: Claude sees ALL issues in one list.
    """
    audits = read_deep_audits(skill_name, limit=1)
    if not audits:
        return scanner_findings

    deep_findings = audits[0].get("findings", [])
    # Tag them so Claude knows which are from deep audit
    for f in deep_findings:
        f["_source"] = "deep_audit"

    return list(scanner_findings) + deep_findings


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Deep Audit — 语义级代码审查。发现 scanner (regex) 看不到的问题。"
    )
    sub = parser.add_subparsers(dest="cmd")

    # brief — generate audit brief for Claude
    p = sub.add_parser("brief", help="生成结构化审计简报 (供 Claude 审查)")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--category", choices=list(AUDIT_CATEGORIES.keys()),
                   help="仅生成指定类别的简报")
    p.add_argument("--output", default="data/deep_audit_brief.json",
                   help="输出文件路径")
    p.add_argument("--compact", action="store_true",
                   help="紧凑模式——不包含完整源码")

    # record — record Claude's deep audit findings
    p = sub.add_parser("record", help="记录 Claude 发现的语义缺陷")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--findings", required=True,
                   help="JSON 文件路径或 JSON 字符串")

    # check — check if deep audit should run
    p = sub.add_parser("check", help="检查是否需要运行深层审计")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--min-rounds", type=int, default=5)

    # prescan — quick heuristic scan (no Claude needed)
    p = sub.add_parser("prescan", help="快速预扫描——自动发现疑似语义缺陷")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--json", action="store_true")

    # guided — self-audit plan for Claude
    p = sub.add_parser("guided", help="[Self-Evolution] 生成自主审计计划 (含代码片段+问题)")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--output", default=None, help="输出到文件")

    # merge — merge deep findings with scanner findings
    p = sub.add_parser("merge", help="合并深层发现与扫描器发现")
    p.add_argument("skill", help="目标 skill")

    # list — list recent deep audits
    p = sub.add_parser("list", help="列出最近的深层审计记录")
    p.add_argument("skill", help="目标 skill")
    p.add_argument("--limit", type=int, default=5)

    args = parser.parse_args()

    if args.cmd == "brief":
        brief = generate_audit_brief(args.skill)
        if args.category:
            cat = AUDIT_CATEGORIES[args.category]
            brief["categories"] = {args.category: brief["categories"][args.category]}
        if args.compact:
            brief.pop("engine_sources", None)

        output_path = Path(args.output)
        if not output_path.is_absolute():
            root = _find_skill_root(args.skill)
            if root:
                output_path = root / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(brief, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        categories_in = list(brief["categories"].keys())
        total_lines = brief["meta"].get("total_lines", 0)
        files = brief["meta"].get("total_files", 0)
        print(f"[OK] 审计简报已生成: {output_path}")
        print(f"  文件: {files} 个引擎文件 ({total_lines} 行)")
        print(f"  类别: {categories_in}")
        if not args.compact:
            print(f"  大小: {len(json.dumps(brief, ensure_ascii=False))} 字符")
        print(f"  下一步: Claude 阅读此简报 → 审查引擎源码 → 输出 findings JSON")
        print(f"  然后: python engine/deep_audit.py {args.skill} record --findings <findings.json>")

    elif args.cmd == "record":
        import json as _json
        findings_data = args.findings
        # Try as file path first, then as JSON string
        fp = Path(findings_data)
        if fp.exists():
            findings = _json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(findings, dict):
                findings = findings.get("findings", [])
        else:
            try:
                findings = _json.loads(findings_data)
                if isinstance(findings, dict):
                    findings = findings.get("findings", [])
            except _json.JSONDecodeError:
                print(f"[!!] 无法解析 findings: 不是有效的 JSON 也不是文件路径")
                sys.exit(1)

        result = record_deep_audit_findings(args.skill, findings)
        print(f"  {result['verdict']}")
        print(f"  严重: {result['critical']} | 总计: {result['recorded']}")

    elif args.cmd == "check":
        result = should_run_deep_audit(args.skill, args.min_rounds)
        icon = "[!!]" if result["should_run"] else "[OK]"
        print(f"  {icon} {result['reason']}")

    elif args.cmd == "prescan":
        result = quick_semantic_scan(args.skill)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"  {result['verdict']}")
            for fpath, reasons in result.get("suspicious", {}).items():
                print(f"\n  📁 {fpath}")
                for r in reasons:
                    print(f"    ⚠️  {r}")

    elif args.cmd == "guided":
        plan = generate_guided_audit(args.skill)
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        elif args.output:
            out_path = Path(args.output)
            if not out_path.is_absolute():
                root = _find_skill_root(args.skill)
                if root:
                    out_path = root / args.output
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(plan, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8")
            print(f"[OK] Guided audit plan written to {out_path}")
        else:
            print_guided_audit(plan)

    elif args.cmd == "merge":
        from engine.system1 import scanner
        scan_result = scanner.scan_skill(args.skill)
        scanner_findings = scan_result.get("findings", [])
        merged = merge_deep_findings(args.skill, scanner_findings)
        print(f"  Scanner findings: {len(scanner_findings)}")
        deep_count = len([f for f in merged if f.get("_source") == "deep_audit"])
        print(f"  Deep audit findings: {deep_count}")
        print(f"  Total: {len(merged)}")

    elif args.cmd == "list":
        audits = read_deep_audits(args.skill, args.limit)
        for a in audits:
            print(f"  [{a['audit_id']}] {a.get('iso_audited', '?')[:19]}")
            print(f"    发现: {a['findings_count']} | 类别: {a.get('categories_checked', [])}")
            if a.get('findings'):
                for f in a['findings'][:3]:
                    cat = f.get('deep_audit', {}).get('category', '?')
                    print(f"    [{cat}] {f.get('file', '?')}:{f.get('line', '?')}")
                    print(f"      {f.get('description', '')[:120]}")

    else:
        parser.print_help()
        print(f"\n  v4 决策树中的位置:")
        print(f"    每 N 轮: python engine/deep_audit.py <skill> prescan")
        print(f"    → 如果有疑似问题 → python engine/deep_audit.py <skill> brief")
        print(f"    → Claude 审查简报 → 输出 findings JSON")
        print(f"    → python engine/deep_audit.py <skill> record --findings <file>")


if __name__ == "__main__":
    main()
