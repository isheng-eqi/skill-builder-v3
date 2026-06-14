#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""System 2: Code Generator — 把 fix_strategy 蓝图翻译成可执行 Python 代码。

这是自进化工厂的"施工队"。rule_generator 画了蓝图 (fix_strategy)，Reflect 签了审批，
challenger 做了安全审查 —— 但需要有人把自然语言描述变成能跑的 Python 函数。

核心设计理念（自指闭环）:
  1. 生成的代码不直接修改 engine/ 源码 —— 存入 data/generated_rules/<id>.py
  2. rules.py 通过动态 import 加载生成的 fix 函数
  3. 生成代码进入试用期 (probation=5)，每次修复都经过 GEPA 执行验证
  4. 试用期满 + 成功率 ≥ 70% → 晋升 auto_apply=True
  5. 试用期失败 ≥ 3 次 → 废弃

LLM 后端抽象:
  - 自动模式: 通过 Claude API 生成代码
  - 人机协作模式: 打印 prompt，接受粘贴的代码
  - 可插拔: 替换 _call_llm() 即可接入任意 LLM 后端
"""

import json
import re
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


# ── Queue helpers (path B: non-blocking autonomous mode) ────────────────────

def _codegen_queue_dir() -> Optional[Path]:
    """Get the pending codegen queue directory."""
    d = SKILL_ROOT / "data" / "pending_codegen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _codegen_queue_size() -> int:
    """How many tasks are waiting in the queue?"""
    qd = _codegen_queue_dir()
    if not qd:
        return 0
    return len(list(qd.glob("codegen-*.json")))


def list_pending_tasks() -> list:
    """List all pending codegen tasks. Oldest first."""
    qd = _codegen_queue_dir()
    if not qd:
        return []
    tasks = []
    for fp in sorted(qd.glob("codegen-*.json")):
        try:
            tasks.append(json.loads(fp.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return tasks


# ── LLM backend abstraction ───────────────────────────────────────────────────

def _call_llm(system_prompt: str, user_prompt: str,
              model: str = "claude-sonnet-4-6") -> tuple:
    """Call LLM to generate code. Returns (success: bool, text: str, error: str).

    Delegates to platform.call_llm() which handles provider resolution.
    Falls back to queue mode or human-in-loop when no API key is configured.
    """
    import os, subprocess
    from engine.platform import call_llm, get_llm_config

    config = get_llm_config()

    # Method 1: Call LLM via platform abstraction
    if config["api_key"]:
        success, text, error = call_llm(system_prompt, user_prompt)
        if success:
            return True, text, ""
        api_error = error
    else:
        api_error = ""

    # Method 2: Queue mode — save to pending_codegen/, don't block
    # This is the KEY change for path B: instead of waiting for human,
    # we save the task and let the Claude Code hook process it next turn
    queue_dir = _codegen_queue_dir()
    if queue_dir:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        task = {
            "iso_queued": datetime.now(timezone.utc).isoformat(),
            "task_id": f"codegen-{ts}",
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "model": model,
            "status": "pending",
        }
        if api_error:
            task["_api_error"] = api_error
        task_path = queue_dir / f"{task['task_id']}.json"
        task_path.write_text(json.dumps(task, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        reason = f"queued → {task_path.name}" + (f" (API: {api_error[:80]})" if api_error else "")
        return False, "", reason

    # Method 3: Human-in-loop fallback (only when queue dir unavailable)
    print(f"\n  {'─'*60}")
    print(f"  [CodeGen] 需要 LLM 生成代码 —— 人机协作模式")
    print(f"  {'─'*60}")
    print(f"\n  === SYSTEM PROMPT ===\n{system_prompt[:2000]}")
    if len(system_prompt) > 2000:
        print(f"  ... (截断, 全长 {len(system_prompt)} 字符)")
    print(f"\n  === USER PROMPT ===\n{user_prompt[:3000]}")
    if len(user_prompt) > 3000:
        print(f"  ... (截断, 全长 {len(user_prompt)} 字符)")
    print(f"\n  {'─'*60}")
    print(f"  请将生成的 Python 代码粘贴到 data/generated_rules/<rule_id>.py")
    print(f"  然后重新运行进化循环。")
    print(f"  {'─'*60}")

    return False, "", "LLM not available — human-in-loop mode"


# ── Context gathering ─────────────────────────────────────────────────────────

def gather_context(skill_name: str, rule: dict) -> dict:
    """Gather all context needed for code generation.

    Returns:
      {
        target_file_content: str,
        existing_fix_functions: list of (name, signature, body_preview),
        rules_registry_format: str (example RULES entry),
        imports_from_target: str,
        fix_strategy: str,
        pattern_name: str,
        rule_name: str,
      }
    """
    from engine.system1 import rules as rules_mod
    from engine.system1 import scanner as scanner_mod

    from engine.platform import find_skill_root
    # Determine target file from rule's file_glob
    file_glob = rule.get("file_glob", "engine/*.py")
    root = find_skill_root(skill_name)

    target_content = ""
    if root:
        candidates = list(root.glob(file_glob))
        if candidates:
            target_content = candidates[0].read_text(encoding="utf-8")[:5000]

    # Existing fix functions for style matching
    fix_functions = []
    import inspect
    for rule_entry in rules_mod.SEED_RULES:
        if "fix" in rule_entry:
            fn = rule_entry["fix"]
            try:
                sig = inspect.signature(fn)
                src = inspect.getsource(fn)
                fix_functions.append({
                    "name": rule_entry["name"],
                    "signature": str(sig),
                    "source": src
                })
            except (OSError, TypeError):
                pass  # TODO: log or re-raise

    # RULES registry format example
    rules_format = """{
        "name": "fix-<name>",
        "pattern": "<pattern-name>",
        "severity": "warning",
        "auto_apply": False,  # Start in probation
        "fix": fix_<name>,
        "description": "<description>"
    },"""

    return {
        "target_content": target_content,
        "fix_functions": fix_functions,
        "rules_format": rules_format,
        "fix_strategy": rule.get("fix_strategy", ""),
        "pattern_name": rule.get("pattern", ""),
        "rule_name": rule.get("name", ""),
        "description": rule.get("description", ""),
        "severity": rule.get("severity", "warning"),
        "file_glob": file_glob,
    }


# ── Prompt construction ───────────────────────────────────────────────────────

def build_prompt(context: dict) -> tuple:
    """Build system + user prompts for code generation.

    The prompts are designed so the LLM produces:
    1. A fix function matching existing style
    2. A RULES registry entry
    3. The code imports nothing beyond what rules.py already has
    """
    existing_functions = "\n".join(
        f"### {f['name']}\n```python\n{f['source']}\n```"
        for f in context.get("fix_functions", [])[:3]
    )

    system = textwrap.dedent(f"""\
    You are a code generator for a self-evolving skill engine written in Python.
    Your task: implement a fix function for a newly discovered anti-pattern.

    ## Rules
    1. Output ONLY valid Python code. No markdown fences, no explanation.
    2. Match the style of existing fix functions (see examples below).
    3. The function receives (root: Path, finding: dict) and returns dict.
    4. The return dict must have "applied": True/False, and either
       "action"/"file" (on success) or "reason"/"error" (on skip/failure).
    5. Read the file content, apply the fix, write it back.
    6. Be conservative: if you can't safely apply the fix, return applied=False.
    7. Always check file existence before reading.
    8. Always write with encoding="utf-8".
    9. MUST include: from pathlib import Path
    10. MUST include: import re (or other stdlib modules needed)

    ## Return format (MUST be exactly this):
    {{"function": "<function definition code with imports>",
      "registry": {{"name": "...", "pattern": "...", "severity": "...",
                    "auto_apply": false, "fix": "<function_name>",
                    "description": "..."}} }}

    ## Existing fix functions (for style reference):
    {existing_functions}
    """)

    target_preview = (context.get("target_content", "") or "# (no target file found)")[:3000]

    user = textwrap.dedent(f"""\
    ## Fix Strategy (natural language blueprint)
    {context.get("fix_strategy", "No strategy provided")}

    ## Metadata
    - Rule name: {context.get("rule_name", "unknown")}
    - Pattern: {context.get("pattern_name", "unknown")}
    - Severity: {context.get("severity", "warning")}
    - Description: {context.get("description", "")}
    - Target file glob: {context.get("file_glob", "*.py")}

    ## Target file content (first 3000 chars):
    ```python
    {target_preview}
    ```

    ## RULES registry entry format:
    ```python
    {context.get("rules_format", "")}
    ```

    Implement the fix function now. Return ONLY the JSON with "function" and "registry" keys.
    """)

    return system, user


# ── Code validation ───────────────────────────────────────────────────────────

def validate_generated_code(code: str, rule: dict, skill_name: str) -> dict:
    """Multi-stage validation of generated code.

    Returns {valid: bool, errors: list, warnings: list}.
    """
    errors = []
    warnings = []

    # Stage 1: Basic security — reject dangerous patterns
    dangerous = ["__import__", "eval(", "exec(", "compile(", "subprocess",
                 "os.system", "shutil.rmtree", "importlib", "globals()",
                 "locals()", "getattr(__", "setattr(__"]
    for d in dangerous:
        if d in code:
            errors.append(f"Security: dangerous pattern '{d}' detected")
            return {"valid": False, "errors": errors, "warnings": warnings}

    # Stage 2: Syntax check
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
        return {"valid": False, "errors": errors, "warnings": warnings}

    # Stage 3: Must contain a function definition
    func_match = re.search(r'def\s+(\w+)\s*\(', code)
    if not func_match:
        errors.append("No function definition found in generated code")
        return {"valid": False, "errors": errors, "warnings": warnings}
    func_name = func_match.group(1)

    # Stage 4: Function signature check
    if "root" not in code or "finding" not in code:
        warnings.append("Function may not accept standard (root, finding) parameters")

    # Stage 5: Must have applied=True/False return
    if "applied" not in code:
        warnings.append("Function may not return standard {'applied': ...} dict")

    # Stage 6: Must have encoding='utf-8' for file writes
    if "write_text" in code and "encoding" not in code:
        warnings.append("write_text() called without encoding='utf-8'")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "function_name": func_name
    }


# ── Code installation ─────────────────────────────────────────────────────────

def install_generated_code(code: str, rule: dict, skill_name: str) -> Optional[Path]:
    """Save generated code to data/generated_rules/<rule_id>.py.

    The code is NOT injected into rules.py. Instead, rules.py dynamically
    loads generated fix functions through rule_generator.merge_generated_rules().
    """
    from engine.system2.rule_generator import _generated_rules_dir

    gd = _generated_rules_dir(skill_name)
    if not gd:
        return None

    # Write the generated Python file
    py_path = gd / f"{rule['rule_id']}.py"
    header = (
        f"# Generated by code_generator.py at {datetime.now(timezone.utc).isoformat()}\n"
        f"# Rule: {rule.get('name', '?')}\n"
        f"# Pattern: {rule.get('pattern', '?')}\n"
        f"# Status: probation\n"
        f"# DO NOT EDIT MANUALLY — will be overwritten by code_generator\n"
        f"\n"
    )
    py_path.write_text(header + code, encoding="utf-8")

    # Update the rule JSON to point to the generated code
    rule["_generated_code_path"] = str(py_path)
    from engine.system2.rule_generator import save_rule
    save_rule(skill_name, rule)

    return py_path


def load_generated_fix(rule: dict) -> Optional[callable]:
    """Dynamically load a generated fix function from its .py file.

    Returns the fix function if successful, None otherwise.
    """
    code_path = rule.get("_generated_code_path", "")
    if not code_path:
        # Try to infer path
        from engine.system2.rule_generator import _generated_rules_dir
        # We don't have skill_name here, so check the path directly
        return None

    py_path = Path(code_path)
    if not py_path.exists():
        return None

    try:
        code = py_path.read_text(encoding="utf-8")
        # Extract function name from code
        func_match = re.search(r'def\s+(\w+)\s*\(', code)
        if not func_match:
            return None
        func_name = func_match.group(1)

        # Execute the code in a clean namespace
        namespace = {}
        exec(compile(code, str(py_path), "exec"), namespace)
        return namespace.get(func_name)
    except Exception:
        return None


# ── Full pipeline ─────────────────────────────────────────────────────────────

def generate_and_install(rule: dict, skill_name: str,
                          auto_mode: bool = False) -> dict:
    """Full code generation pipeline: prompt → LLM → validate → install.

    This is the main entry point. Called by _reflect_and_extend_scanner()
    when a new generated rule needs executable fix code.

    Args:
        rule: Generated rule dict from rule_generator
        skill_name: Target skill name
        auto_mode: If True, try API first. If False, always use human-in-loop.

    Returns:
        {generated: bool, code_path: str, errors: list, warnings: list}
    """
    print(f"  [CodeGen] 为规则 '{rule.get('name', '?')}' 生成修复代码...")

    # 1. Gather context
    context = gather_context(skill_name, rule)
    print(f"    上下文: {len(context.get('target_content', ''))} 字符目标代码, "
          f"{len(context.get('fix_functions', []))} 个参考函数")

    # 2. Build prompt
    system_prompt, user_prompt = build_prompt(context)

    # 3. Call LLM
    success, response, error = _call_llm(system_prompt, user_prompt)
    if not success:
        return {
            "generated": False,
            "code_path": "",
            "errors": [error],
            "warnings": [],
            "verdict": f"[--] LLM 不可用: {error}"
        }

    # 4. Parse response
    try:
        # Extract JSON from response (may be wrapped in fences or text)
        json_match = re.search(r'\{.*"function".*\}', response, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
        else:
            parsed = json.loads(response)
        code = parsed.get("function", "")
        registry = parsed.get("registry", {})
    except (json.JSONDecodeError, AttributeError):
        return {
            "generated": False,
            "code_path": "",
            "errors": ["Failed to parse LLM response as JSON"],
            "warnings": [],
            "raw_response": response[:500]
        }

    if not code:
        return {
            "generated": False,
            "code_path": "",
            "errors": ["LLM returned empty function code"],
            "warnings": []
        }

    # 5. Validate
    validation = validate_generated_code(code, rule, skill_name)
    if not validation["valid"]:
        return {
            "generated": False,
            "code_path": "",
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "verdict": f"[!!] 代码验证失败: {'; '.join(validation['errors'])}"
        }
    if validation.get("warnings"):
        print(f"    [!] 警告: {'; '.join(validation['warnings'])}")

    # 6. Install
    code_path = install_generated_code(code, rule, skill_name)
    if not code_path:
        return {
            "generated": False,
            "code_path": "",
            "errors": ["Failed to install generated code"],
            "warnings": []
        }

    print(f"    → 代码已安装: {code_path}")
    print(f"    → 函数名: {validation.get('function_name', '?')}")
    print(f"    → 试用期: 剩余 {rule.get('probation', {}).get('remaining', 5)} 次")

    # Record event
    from engine import memory
    memory.write_event(skill_name, "code_generated", {
        "rule_id": rule.get("rule_id", "?"),
        "rule_name": rule.get("name", "?"),
        "code_path": str(code_path),
        "function_name": validation.get("function_name", "?"),
        "validation_warnings": validation.get("warnings", [])
    })

    return {
        "generated": True,
        "code_path": str(code_path),
        "errors": [],
        "warnings": validation.get("warnings", []),
        "function_name": validation.get("function_name", "?"),
        "verdict": f"[OK] 代码生成成功 → 试用期"
    }


def try_generate_for_pending_rules(skill_name: str) -> dict:
    """Check for probation rules that don't have generated code yet,
    and try to generate code for them.

    Called at the end of each evolution loop.
    """
    from engine.system2 import rule_generator as rg

    probation_rules = rg.get_probation_rules(skill_name)
    pending = [r for r in probation_rules
               if not r.get("_generated_code_path")
               or not Path(r.get("_generated_code_path", "")).exists()]

    if not pending:
        return {"generated": 0, "rules": []}

    print(f"\n  [CodeGen] {len(pending)} 个试用规则缺少可执行代码")

    results = []
    for rule in pending:
        result = generate_and_install(rule, skill_name, auto_mode=False)
        results.append(result)

    generated = sum(1 for r in results if r.get("generated"))

    return {
        "generated": generated,
        "pending": len(pending),
        "results": results,
        "verdict": f"[OK] {generated}/{len(pending)} 规则已生成代码" if generated
                   else f"[--] {len(pending)} 规则等待 LLM 可用"
    }


# ── Queue processor (path B: autonomous mode) ───────────────────────────────

def process_pending_queue(skill_name: str = "skill-builder-v3",
                          code: Optional[str] = None) -> dict:
    """Process pending codegen queue. Called by Claude Code hook or manually.

    In Claude Code sessions, the hook passes the generated code directly.
    In standalone mode with API key, _call_llm() handles it inline.
    In standalone without API: tasks just queue up for next session.

    Args:
        skill_name: Target skill
        code: If provided (by hook), use this code directly for the oldest task.
              If None, tries API key, then reports queue status.

    Returns:
        {processed: int, remaining: int, tasks: list}
    """
    tasks = list_pending_tasks()
    if not tasks:
        return {"processed": 0, "remaining": 0, "tasks": [],
                "verdict": "[OK] 队列为空"}

    processed = 0
    results = []

    for task in tasks:
        if task.get("status") != "pending":
            continue

        generated_code = code  # Hook-provided code (one per call)

        if not generated_code:
            # Try API
            success, response, error = _call_llm(
                task["system_prompt"], task["user_prompt"], task.get("model", "claude-sonnet-4-6")
            )
            if success:
                generated_code = response
            else:
                # Can't process — keep in queue
                results.append({
                    "task_id": task["task_id"],
                    "status": "kept_in_queue",
                    "reason": error
                })
                continue

        if not generated_code:
            continue

        # Parse the generated code
        import re as _re
        try:
            json_match = _re.search(r'\{.*"function".*\}', generated_code, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
            else:
                parsed = json.loads(generated_code)
            func_code = parsed.get("function", "")
        except (json.JSONDecodeError, AttributeError):
            results.append({
                "task_id": task["task_id"],
                "status": "parse_failed",
                "reason": "cannot extract JSON from response"
            })
            _mark_task_done(task["task_id"], "parse_failed")
            processed += 1
            continue

        if not func_code:
            _mark_task_done(task["task_id"], "no_code")
            processed += 1
            continue

        # Find the rule this task is for by matching description
        from engine.system2 import rule_generator as rg
        all_rules = rg.load_all_generated_rules(skill_name)
        target_rule = None
        for r in all_rules:
            if r.get("status") == "deprecated":
                continue
            if not r.get("_generated_code_path"):
                target_rule = r
                break
        # Fallback: match by pattern name in prompt
        if not target_rule:
            for r in all_rules:
                if r.get("status") == "deprecated":
                    continue
                pattern = r.get("pattern", "")
                if pattern and pattern in task.get("user_prompt", ""):
                    target_rule = r
                    break

        if not target_rule:
            results.append({
                "task_id": task["task_id"],
                "status": "no_target_rule",
                "reason": "cannot find matching probation rule"
            })
            _mark_task_done(task["task_id"], "no_target")
            processed += 1
            continue

        # Validate
        validation = validate_generated_code(func_code, target_rule, skill_name)
        if not validation["valid"]:
            results.append({
                "task_id": task["task_id"],
                "rule": target_rule.get("name", "?"),
                "status": "validation_failed",
                "errors": validation["errors"]
            })
            _mark_task_done(task["task_id"], "validation_failed")
            processed += 1
            continue

        # Install
        from engine.system2.rule_generator import _generated_rules_dir as _rgd, save_rule as _sr
        gd = _rgd(skill_name)
        if not gd:
            results.append({"task_id": task["task_id"], "status": "failed", "reason": "no rules dir"})
            continue

        py_path = gd / f"{target_rule['rule_id']}.py"
        header = (
            f"# Generated by Claude Code hook (autonomous mode) at {datetime.now(timezone.utc).isoformat()}\n"
            f"# Rule: {target_rule.get('name', '?')}\n"
            f"# Pattern: {target_rule.get('pattern', '?')}\n"
            f"# Status: probation\n\n"
        )
        py_path.write_text(header + func_code, encoding="utf-8")

        target_rule["_generated_code_path"] = str(py_path)
        _sr(skill_name, target_rule)

        _mark_task_done(task["task_id"], "installed")
        processed += 1
        results.append({
            "task_id": task["task_id"],
            "rule": target_rule.get("name", "?"),
            "status": "installed",
            "code_path": str(py_path),
            "function_name": validation.get("function_name", "?")
        })

        # Only process one task per hook call (safety: don't auto-generate a flood)
        break

    remaining = _codegen_queue_size()
    return {
        "processed": processed,
        "remaining": remaining,
        "results": results,
        "verdict": (f"[OK] {processed} tasks processed, {remaining} remaining"
                    if processed else f"[--] {remaining} tasks queued (waiting for LLM)")
    }


def _mark_task_done(task_id: str, status: str):
    """Mark a queue task as done (rename to .done)."""
    qd = _codegen_queue_dir()
    if not qd:
        return
    task_path = qd / f"{task_id}.json"
    if task_path.exists():
        done_path = qd / f"{task_id}.done"
        task_path.rename(done_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="System 2 代码生成器 —— 把 fix_strategy 翻译成 Python 代码"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("generate", help="为指定规则生成代码")
    p.add_argument("skill", help="目标 skill 名称")
    p.add_argument("--rule-id", required=True, help="规则 ID")

    p = sub.add_parser("pending", help="为所有待生成代码的试用规则生成代码")
    p.add_argument("skill", help="目标 skill 名称")

    p = sub.add_parser("process-queue", help="(Path B) 处理排队中的代码生成任务")
    p.add_argument("skill", help="目标 skill 名称")

    p = sub.add_parser("queue-status", help="(Path B) 查看排队状态")
    p.add_argument("skill", help="目标 skill 名称")

    args = parser.parse_args()

    from engine.system2 import rule_generator as rg

    if args.cmd == "generate":
        rule = rg.load_rule(args.skill, args.rule_id)
        if not rule:
            print(f"[!!] 规则不存在: {args.rule_id}")
            sys.exit(1)
        result = generate_and_install(rule, args.skill)
        print(f"\n  {result.get('verdict', '?')}")
        if result.get("errors"):
            for e in result["errors"]:
                print(f"    [!] {e}")
        if result.get("code_path"):
            print(f"    文件: {result['code_path']}")
        sys.exit(0 if result["generated"] else 1)

    elif args.cmd == "pending":
        result = try_generate_for_pending_rules(args.skill)
        print(f"\n  {result['verdict']}")
        sys.exit(0)

    elif args.cmd == "process-queue":
        result = process_pending_queue(args.skill)
        print(f"\n  {result['verdict']}")
        for r in result.get("results", []):
            icon = "[OK]" if r.get("status") == "installed" else "[--]"
            print(f"  {icon} {r.get('task_id','?')[:30]}: {r.get('status','?')} "
                  f"→ {r.get('rule', r.get('reason','?'))}")
        sys.exit(0 if result["processed"] > 0 else 1 if result["remaining"] > 0 else 0)

    elif args.cmd == "queue-status":
        tasks = list_pending_tasks()
        print(f"\n  排队任务: {len(tasks)}")
        for t in tasks:
            print(f"  [?] {t.get('task_id','?')[:40]}: {t.get('iso_queued','?')}")
        sys.exit(0)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
