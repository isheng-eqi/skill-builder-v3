#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Event-sourced memory for skill-builder v3.

Three tiers of memory, inspired by Generative Agents:
  Tier 1: Event Log — immutable, append-only, timestamped (the Memory Stream)
  Tier 2: Insights — LLM-synthesized abstractions from event patterns (Reflections)
  Tier 3: Metrics — time-series proxy measurements for parameter tuning

v1.1 additions (2026-06-13):
  - execution_trace events (GEPA: verify fix by running skill)
  - global_insights store (HyperAgents: cross-skill meta-level transfer)
  - fix_quality per rule (TAPO: per-turn credit assignment)
  - generator_noise / verifier_snr metrics (GVU: stability monitoring)
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Path resolution ───────────────────────────────────────────────────────────

# Global data lives under the skill-builder that's currently executing.
# When running as a different skill, use the executing skill's data dir.
def _global_data_dir() -> Path:
    """Resolve the global data directory from the executing skill's location."""
    # The module's own path tells us which skill is executing
    engine_dir = Path(__file__).resolve().parent  # .../skills/<skill-name>/engine/
    return engine_dir.parent / "data"

def _skill_root(skill_name: str) -> Optional[Path]:
    """Find a skill's root directory. Searches ~/.claude/skills/<name>."""
    base = Path(os.environ.get("SB3_SKILLS_DIR", str(Path.home() / ".claude" / "skills")))
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            return d
    return None


def _data_dir(skill_name: str) -> Optional[Path]:
    root = _skill_root(skill_name)
    return root / "data" if root else None


# ── Tier 1: Event Log ─────────────────────────────────────────────────────────

EVENT_TYPES = {
    "scan",             # System 1 fast scan completed
    "fix",              # A deterministic fix was applied
    "loop",             # One Layer 1 cycle completed
    "reflect",          # Layer 2 reflection ran
    "propose",          # Architecture change proposed
    "verify",           # Proposal verified (pass or fail)
    "integrate",        # Change integrated with snapshot
    "rollback",         # Change rolled back
    "insight",          # New insight synthesized
    "graduate",         # Autonomy level changed
    "execution_trace",  # (GEPA) Result of verifying a fix by actually running the skill
    "global_insight",   # (HyperAgents) Cross-skill meta-level insight
    "evidence_span",    # (EVE-Agent v1.2) 修复证据——为什么这项修复被认为有效
    "rule_generated",   # (Gödel Agent v1.2) 新规则自动生成
    "code_generated",   # (CodeGen v1.3) LLM 生成了规则的修复代码
}


def write_event(skill_name: str, event_type: str, data: dict) -> Optional[Path]:
    """Write an immutable event to the event log. Returns the file path."""
    assert event_type in EVENT_TYPES, f"Unknown event type: {event_type}"

    dd = _data_dir(skill_name)
    if not dd:
        return None

    logs_dir = dd / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    event = {
        "event_id": f"evt-{ts}",
        "event_type": event_type,
        "iso_timestamp": datetime.now(timezone.utc).isoformat(),
        "skill": skill_name,
        "data": data
    }

    fp = logs_dir / f"{ts}_{event_type}.json"
    fp.write_text(json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def read_events(skill_name: str, event_type: Optional[str] = None,
                limit: int = 50) -> list:
    """Read events from the log. Newest first."""
    dd = _data_dir(skill_name)
    if not dd:
        return []
    logs_dir = dd / "logs"
    if not logs_dir.exists():
        return []

    files = sorted(logs_dir.glob("*.json"), reverse=True)
    events = []
    for fp in files:
        try:
            evt = json.loads(fp.read_text(encoding="utf-8"))
            if event_type and evt.get("event_type") != event_type:
                continue
            events.append(evt)
            if len(events) >= limit:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return events


def read_events_since(skill_name: str, since_iso: str,
                       event_type: Optional[str] = None) -> list:
    """Read events newer than a given ISO timestamp."""
    events = read_events(skill_name, event_type=event_type, limit=500)
    try:
        since = datetime.fromisoformat(since_iso)
    except (ValueError, TypeError):
        return events
    return [e for e in events
            if datetime.fromisoformat(e["iso_timestamp"]) > since]


# ── Tier 2: Insights ──────────────────────────────────────────────────────────

def write_insight(skill_name: str, insight_text: str, evidence_events: list,
                  confidence: float = 0.5) -> Optional[Path]:
    """Write a synthesized insight to the insights store.

    Insights are compressed abstractions from event patterns.
    They correspond to Generative Agents' "Reflections" — higher-level
    inferences from raw experiences.
    """
    dd = _data_dir(skill_name)
    if not dd:
        return None

    insights_dir = dd / "insights"
    insights_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    insight = {
        "insight_id": f"ins-{ts}",
        "iso_created": datetime.now(timezone.utc).isoformat(),
        "text": insight_text,
        "confidence": confidence,
        "evidence_event_ids": [e.get("event_id", "?") for e in evidence_events[:10]],
        "status": "active"
    }

    fp = insights_dir / f"{ts}.json"
    fp.write_text(json.dumps(insight, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def read_insights(skill_name: str, status: str = "active", limit: int = 20) -> list:
    """Read insights. Newest first."""
    dd = _data_dir(skill_name)
    if not dd:
        return []
    insights_dir = dd / "insights"
    if not insights_dir.exists():
        return []

    files = sorted(insights_dir.glob("*.json"), reverse=True)
    insights = []
    for fp in files:
        try:
            ins = json.loads(fp.read_text(encoding="utf-8"))
            if ins.get("status") == status:
                insights.append(ins)
                if len(insights) >= limit:
                    break
        except (json.JSONDecodeError, OSError):
            continue
    return insights


# ── Global Insights (HyperAgents: cross-skill meta-level transfer) ────────────

def _global_insights_dir() -> Path:
    """Get the global insights directory (shared across all skills)."""
    d = _global_data_dir() / "global_insights"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_global_insight(insight_text: str, affected_skills: list,
                          pattern_name: str = "",
                          confidence: float = 0.5,
                          source_skill: str = "") -> Optional[Path]:
    """(HyperAgents) Write a cross-skill meta-level insight.

    These are patterns discovered in one skill that may apply to others.
    The insight is stored in a shared location accessible to all skill audits.

    Args:
        insight_text: The insight description
        affected_skills: List of skill names this applies to
        pattern_name: Optional pattern name for indexing
        confidence: 0.0-1.0 confidence score
        source_skill: The skill where this was discovered
    """
    gid = _global_insights_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    insight = {
        "global_insight_id": f"gins-{ts}",
        "iso_created": datetime.now(timezone.utc).isoformat(),
        "text": insight_text,
        "pattern_name": pattern_name,
        "affected_skills": affected_skills,
        "source_skill": source_skill,
        "confidence": confidence,
        "application_count": 0,
        "last_applied_iso": None,
        "status": "active"
    }

    fp = gid / f"{ts}.json"
    fp.write_text(json.dumps(insight, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also record as event in source skill if available
    if source_skill:
        write_event(source_skill, "global_insight", {
            "global_insight_id": insight["global_insight_id"],
            "pattern_name": pattern_name,
            "affected_skills": affected_skills
        })

    return fp


def read_global_insights(status: str = "active", limit: int = 50) -> list:
    """Read cross-skill global insights. Newest first."""
    gid = _global_insights_dir()
    files = sorted(gid.glob("*.json"), reverse=True)
    insights = []
    for fp in files:
        try:
            ins = json.loads(fp.read_text(encoding="utf-8"))
            if ins.get("status") == status:
                insights.append(ins)
                if len(insights) >= limit:
                    break
        except (json.JSONDecodeError, OSError):
            continue
    return insights


def mark_global_insight_applied(global_insight_id: str) -> bool:
    """Increment application count for a global insight."""
    gid = _global_insights_dir()
    for fp in sorted(gid.glob("*.json"), reverse=True):
        try:
            ins = json.loads(fp.read_text(encoding="utf-8"))
            if ins.get("global_insight_id") == global_insight_id:
                ins["application_count"] = ins.get("application_count", 0) + 1
                ins["last_applied_iso"] = datetime.now(timezone.utc).isoformat()
                fp.write_text(json.dumps(ins, indent=2, ensure_ascii=False), encoding="utf-8")
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


# ── Tier 3: Metrics ──────────────────────────────────────────────────────────

def write_metric(skill_name: str, metric_name: str, value: float,
                 meta: Optional[dict] = None) -> Optional[Path]:
    """Write a metric data point. Used for proxy metric time series."""
    dd = _data_dir(skill_name)
    if not dd:
        return None

    metrics_dir = dd / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    # Append to metric-specific JSONL file
    fp = metrics_dir / f"{metric_name}.jsonl"
    record = {
        "iso": datetime.now(timezone.utc).isoformat(),
        "value": value,
        "meta": meta or {}
    }
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return fp


def read_metric_series(skill_name: str, metric_name: str) -> list:
    """Read the full time series for a metric."""
    dd = _data_dir(skill_name)
    if not dd:
        return []
    fp = dd / "metrics" / f"{metric_name}.jsonl"
    if not fp.exists():
        return []

    records = []
    for line in fp.read_text(encoding="utf-8").strip().split("\n"):
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def get_latest_metric(skill_name: str, metric_name: str,
                       default: float = 0.0) -> float:
    """Get the most recent value of a metric."""
    series = read_metric_series(skill_name, metric_name)
    return series[-1]["value"] if series else default


# ── TAPO: Fix quality tracking per rule ───────────────────────────────────────

def write_fix_quality(skill_name: str, rule_name: str,
                       success: bool, context: Optional[dict] = None) -> Optional[Path]:
    """(TAPO) Record per-rule fix quality for per-turn credit assignment.

    Each fix gets a quality data point. When correlated with rollbacks,
    this enables automatic confidence adjustment for problematic rules.
    """
    dd = _data_dir(skill_name)
    if not dd:
        return None

    quality_dir = dd / "fix_quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    fp = quality_dir / f"{rule_name}.jsonl"
    record = {
        "iso": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "context": context or {}
    }
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return fp


def get_fix_quality(skill_name: str, rule_name: str,
                     lookback: int = 20) -> dict:
    """Get per-rule fix quality stats for recent fixes."""
    dd = _data_dir(skill_name)
    if not dd:
        return {"rule": rule_name, "total": 0, "success_rate": 1.0}

    fp = dd / "fix_quality" / f"{rule_name}.jsonl"
    if not fp.exists():
        return {"rule": rule_name, "total": 0, "success_rate": 1.0}

    records = []
    for line in fp.read_text(encoding="utf-8").strip().split("\n"):
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    recent = records[-lookback:]
    if not recent:
        return {"rule": rule_name, "total": 0, "success_rate": 1.0}

    successes = sum(1 for r in recent if r.get("success"))
    return {
        "rule": rule_name,
        "total": len(recent),
        "successes": successes,
        "failures": len(recent) - successes,
        "success_rate": successes / len(recent),
        "last_10_rate": sum(1 for r in recent[-10:] if r.get("success")) / min(10, len(recent))
    }


def get_all_fix_qualities(skill_name: str) -> dict:
    """Get fix quality for all rules that have data."""
    dd = _data_dir(skill_name)
    if not dd:
        return {}

    quality_dir = dd / "fix_quality"
    if not quality_dir.exists():
        return {}

    # descendant_links.jsonl uses a different schema (causal graph, not quality)
    _NON_QUALITY_FILES = {"descendant_links"}
    qualities = {}
    for fp in sorted(quality_dir.glob("*.jsonl")):
        if fp.stem in _NON_QUALITY_FILES:
            continue
        rule_name = fp.stem
        qualities[rule_name] = get_fix_quality(skill_name, rule_name)
    return qualities


# ── GVU: Verifier SNR monitoring ──────────────────────────────────────────────

def calculate_gvu_snr(skill_name: str, lookback_rounds: int = 10) -> dict:
    """(GVU Operator) Calculate generator noise vs verifier SNR.

    Generator noise = rate of findings that were flagged but NOT actionable
    Verifier SNR = rate of findings correctly identified as needing a fix

    Based on Chojecki's Variance Inequality:
      self-improvement is stable iff verifier SNR > generator noise

    Returns a dict with the GVU stability assessment.
    """
    events = read_events(skill_name, limit=lookback_rounds * 10)

    # Count fix events and their outcomes
    fix_events = [e for e in events if e["event_type"] == "fix"]
    rollback_events = [e for e in events if e["event_type"] == "rollback"]
    scan_events = [e for e in events if e["event_type"] == "scan"]

    total_fixes = len(fix_events)
    total_rollbacks = len(rollback_events)
    total_findings = sum(
        e.get("data", {}).get("findings_count", 0)
        for e in scan_events
    )

    if total_findings == 0:
        # v1.2: 不再返回虚假的 1.0。检查 execution_traces 获取真实 SNR
        exec_traces = [e for e in events if e["event_type"] == "execution_trace"]
        trace_successes = sum(1 for t in exec_traces
                              if t.get("data", {}).get("success", False))
        trace_total = len(exec_traces)

        if trace_total >= 3:
            import math
            trace_snr = trace_successes / trace_total
            verifier_snr = trace_snr
            verifier_source = "execution_traces"
            stability_ratio = float("inf")  # noise=0, cannot divide
            stable = verifier_snr > 0.7
        elif trace_total > 0:
            verifier_snr = trace_successes / trace_total
            verifier_source = "execution_traces_insufficient"
            stability_ratio = float("inf")
            stable = True  # Give benefit of doubt with some data
        else:
            # No findings AND no traces — truly no data
            verifier_snr = None  # v1.2: None 代替假 1.0
            verifier_source = "no_data"
            stability_ratio = float("inf")
            stable = True

        return {
            "generator_noise": 0.0,
            "verifier_snr": verifier_snr,
            "stability_ratio": stability_ratio,
            "stable": stable,
            "verdict": "[--] GVU: 无 findings——SNR 无法从 fix 数据计算" if verifier_snr is None else
                       f"[OK] GVU: 基于 {trace_total} 条执行轨迹 (SNR={verifier_snr:.2f})",
            "details": {
                "total_findings": 0,
                "total_fixes": total_fixes,
                "total_rollbacks": total_rollbacks,
                "execution_traces": trace_total,
                "trace_successes": trace_successes,
                "generator_noise": 0.0,
                "verifier_snr": verifier_snr,
                "verifier_source": verifier_source,
                "stability_ratio": "∞"
            }
        }

    # Generator noise: findings that were NOT fixed (false positives from scanner)
    findings_not_fixed = max(0, total_findings - total_fixes)
    generator_noise = findings_not_fixed / total_findings if total_findings > 0 else 0.0

    # Verifier SNR: blend rollback-based signal with independent execution traces
    # (GEPA) execution_trace events provide independent ground truth
    exec_traces = [e for e in events if e["event_type"] == "execution_trace"]
    trace_successes = sum(1 for t in exec_traces
                          if t.get("data", {}).get("success", False))
    trace_total = len(exec_traces)

    # Rollback-based estimate (legacy — only captures catastrophic failures)
    rollback_incorrect = total_rollbacks
    rollback_correct = max(0, total_fixes - rollback_incorrect)
    rollback_snr = rollback_correct / total_fixes if total_fixes > 0 else 0.0

    # Blend with execution trace signal when available (GEPA independent verification)
    if trace_total >= 3:
        trace_snr = trace_successes / trace_total
        # Geometric mean of the two independent signals
        import math
        verifier_snr = math.sqrt(max(0.01, rollback_snr) * trace_snr)
        verifier_source = "blended"
    elif total_fixes > 0:
        # No independent traces — SNR relies solely on rollback data
        verifier_snr = rollback_snr
        verifier_source = "rollback_only" if total_rollbacks > 0 else "unverified"
    else:
        verifier_snr = 0.0
        verifier_source = "no_data"

    # Stability ratio (Chojecki's Inequality)
    stability_ratio = verifier_snr / generator_noise if generator_noise > 0 else float("inf")
    stable = stability_ratio > 1.0

    details = {
        "total_findings": total_findings,
        "total_fixes": total_fixes,
        "total_rollbacks": total_rollbacks,
        "execution_traces": trace_total,
        "trace_successes": trace_successes,
        "generator_noise": round(generator_noise, 3),
        "verifier_snr": round(verifier_snr, 3),
        "verifier_source": verifier_source,
        "stability_ratio": round(stability_ratio, 2) if stability_ratio != float("inf") else "∞"
    }

    if verifier_source == "unverified":
        if total_rollbacks == 0 and total_findings == 0:
            # Clean state: no failures, no findings → converged, not "unverified danger"
            verdict = "[OK] GVU: 系统干净——0回滚0发现，无需独立验证"
        elif total_rollbacks == 0:
            verdict = "[OK] GVU: 无回滚记录——系统可能健康或未检测到故障"
        else:
            verdict = (f"[!!] GVU: SNR 未独立验证——仅依赖回滚计数 (SNR={verifier_snr:.2f}, "
                       f"{total_rollbacks} 回滚)")
    elif verifier_source == "no_data":
        verdict = "[--] GVU: 无数据——系统尚在冷启动阶段"
    elif stable:
        verdict = "[OK] 验证器 SNR > 生成器噪声 — 系统稳定"
    else:
        verdict = (f"[!!] 验证器 SNR ({verifier_snr:.2f}) ≤ 生成器噪声 ({generator_noise:.2f})"
                   f" — 系统趋向不稳定，建议暂停自改进等待人工校准")

    return {
        "generator_noise": round(generator_noise, 3),
        "verifier_snr": round(verifier_snr, 3),
        "stability_ratio": stability_ratio,
        "stable": stable,
        "verdict": verdict,
        "details": details
    }


# ── (P6: HGM) Metaproductivity — descendant quality tracking ──────────────────

# Tracks the causal graph: fix A enabled/blocked fix B/C/D.
# HGM (Huxley-Gödel Machine) insight: evaluate rules by the quality of their
# DESCENDANTS, not just their immediate success.

_descendant_links = {}  # {skill_name: [{parent_rule, child_rule, relationship, iso}]}


def record_descendant_link(skill_name: str, parent_rule: str,
                            child_rule: str, relationship: str = "enabled",
                            metadata: Optional[dict] = None) -> Optional[Path]:
    """(P6/HGM) Record a causal link between two fixes.

    parent_rule's fix enabled or blocked child_rule's fix.
    This builds the metaproductivity graph: which rules produce good descendants?

    relationship: "enabled" (parent fix allowed child fix) |
                  "blocked" (parent fix prevented child fix) |
                  "caused_rollback" (parent fix led to child being rolled back)

    Used by HGM metaproductivity scoring: a rule that enables many good
    descendants scores higher than one that only looks good in isolation.
    """
    ts = datetime.now(timezone.utc)
    link = {
        "iso": ts.isoformat(),
        "parent_rule": parent_rule,
        "child_rule": child_rule,
        "relationship": relationship,
        "metadata": metadata or {}
    }

    if skill_name not in _descendant_links:
        _descendant_links[skill_name] = []
    _descendant_links[skill_name].append(link)

    # Persist to disk
    dd = _data_dir(skill_name)
    if dd:
        quality_dir = dd / "fix_quality"
        quality_dir.mkdir(parents=True, exist_ok=True)
        fp = quality_dir / "descendant_links.jsonl"
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(link, ensure_ascii=False) + "\n")

    write_event(skill_name, "fix", {
        "phase": "descendant_link",
        "parent_rule": parent_rule,
        "child_rule": child_rule,
        "relationship": relationship
    })

    return dd / "fix_quality" / "descendant_links.jsonl" if dd else None


def get_descendant_quality(skill_name: str, rule_name: str) -> dict:
    """(P6/HGM) Calculate metaproductivity score for a rule.

    Clade Metaproductivity (CMP): measures the collective output of all
    descendants, not just the rule's immediate success.

    A rule that:
    - Enables 3 successful descendant fixes → CMP boost
    - Caused 2 rollbacks in descendant fixes → CMP penalty
    - Has no descendants → neutral CMP

    Returns {rule, immediate_success_rate, descendant_count,
             enabled_count, caused_rollback_count, cmp_score}.
    """
    immediate = get_fix_quality(skill_name, rule_name)
    imm_rate = immediate.get("success_rate", 1.0)

    # Collect descendant links
    links = _descendant_links.get(skill_name, [])
    if not links:
        # Try loading from disk
        dd = _data_dir(skill_name)
        if dd:
            fp = dd / "fix_quality" / "descendant_links.jsonl"
            if fp.exists():
                try:
                    for line in fp.read_text(encoding="utf-8").strip().split("\n"):
                        if line:
                            _descendant_links.setdefault(skill_name, []).append(
                                json.loads(line))
                except (json.JSONDecodeError, OSError):
                    pass  # TODO: log or re-raise
        links = _descendant_links.get(skill_name, [])

    parent_links = [l for l in links if l["parent_rule"] == rule_name]
    child_links = [l for l in links if l["child_rule"] == rule_name]

    enabled = [l for l in parent_links if l["relationship"] == "enabled"]
    blocked = [l for l in parent_links if l["relationship"] == "blocked"]
    rollbacks = [l for l in parent_links if l["relationship"] == "caused_rollback"]

    # CMP = base_rate × (1 + enabled_boost - rollback_penalty)
    enabled_boost = len(enabled) * 0.1  # Each enabled descendant adds 10%
    rollback_penalty = len(rollbacks) * 0.15  # Each rollback costs 15%
    descendant_factor = 1.0 + enabled_boost - rollback_penalty

    cmp_score = imm_rate * max(0.1, descendant_factor)

    return {
        "rule": rule_name,
        "immediate_success_rate": imm_rate,
        "descendant_count": len(parent_links),
        "enabled_descendants": len(enabled),
        "caused_rollbacks": len(rollbacks),
        "immediate_rate": round(imm_rate, 3),
        "cmp_score": round(cmp_score, 3),
        "verdict": (
            "[OK] 高产规则——该规则的修复启用了后续成功修复" if cmp_score > imm_rate
            else "[!!] 有害规则——即时成功率好但下游效应差" if cmp_score < imm_rate * 0.5
            else "[--] 中性——下游效应与即时成功率一致"
        )
    }


def update_descendant_quality_aggregate(skill_name: str) -> dict:
    """(P6/HGM) Aggregate metaproductivity for all rules.

    Called at session end (hook_session_stop) to update the global view.
    Returns {rule_name: cmp_score} for all rules with descendant data.
    """
    from engine.system1 import rules as rule_engine

    # Get all rules (hardcoded + generated)
    all_rule_names = set(r["name"] for r in rule_engine.SEED_RULES)
    try:
        from engine.system2 import rule_generator
        for r in rule_generator.load_all_generated_rules(skill_name):
            all_rule_names.add(r["name"])
    except ImportError:
        pass  # v4: rule_generator is optional — only used for descendant quality stats. OK to skip.

    qualities = {}
    for rname in all_rule_names:
        dq = get_descendant_quality(skill_name, rname)
        if dq["descendant_count"] > 0:
            qualities[rname] = dq
            write_metric(skill_name, "cmp_score", dq["cmp_score"],
                         {"rule": rname})

    if qualities:
        harmful = [r for r, q in qualities.items() if q["cmp_score"] < q["immediate_rate"] * 0.5]
        if harmful:
            write_insight(
                skill_name,
                f"HGM 元生产力警告: {harmful} 的即时成功率高但下游效应差。"
                f"建议: 降低这些规则的 auto_apply 置信度，或增加 probation 要求。",
                [], confidence=0.75
            )

    return qualities


def _check_file_exists(self, path: str) -> bool:
    """Check if a file exists. (internal helper)"""
    return Path(path).exists()


# ── Statistics helpers ────────────────────────────────────────────────────────

def count_events_by_type(skill_name: str) -> dict:
    """Count events grouped by type."""
    dd = _data_dir(skill_name)
    if not dd:
        return {}
    logs_dir = dd / "logs"
    if not logs_dir.exists():
        return {}

    counts = {}
    for fp in logs_dir.glob("*.json"):
        try:
            evt = json.loads(fp.read_text(encoding="utf-8"))
            t = evt.get("event_type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        except (json.JSONDecodeError, OSError):
            continue
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def get_session_count(skill_name: str) -> int:
    """Count distinct sessions from event logs.

    A new session starts when there's a gap > 1 hour between events,
    or when a 'scan' event follows a 'reflect' event.
    """
    events = read_events(skill_name, limit=500)
    if not events:
        return 0

    sessions = 1
    for i in range(len(events) - 1):
        newer = events[i]
        older = events[i + 1]
        try:
            t_new = datetime.fromisoformat(newer["iso_timestamp"])
            t_old = datetime.fromisoformat(older["iso_timestamp"])
            if (t_new - t_old).total_seconds() > 3600:
                sessions += 1
        except (ValueError, KeyError):
            pass  # TODO: log or re-raise
    return sessions


# ── Audit trail ───────────────────────────────────────────────────────────────

def audit_trail(skill_name: str) -> dict:
    """Check recording integrity: are there fixes without events?
    Returns a report dict.
    """
    events = read_events(skill_name, limit=100)
    fix_events = [e for e in events if e["event_type"] == "fix"]
    total_events = len(events)

    # Check for events with incomplete data
    incomplete = []
    for e in events:
        if not e.get("data") or not e.get("iso_timestamp"):
            incomplete.append(e.get("event_id", "?"))

    # GVU stability check
    gvu = calculate_gvu_snr(skill_name)

    return {
        "total_events": total_events,
        "fix_events": len(fix_events),
        "incomplete_events": len(incomplete),
        "sessions": get_session_count(skill_name),
        "event_types": count_events_by_type(skill_name),
        "gvu_stability": gvu,
        "verdict": ("[OK] Recording integrity verified" if not incomplete
                    else f"[!!] {len(incomplete)} incomplete events")
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Event-sourced memory system for skill-builder v3")
    sub = parser.add_subparsers(dest="cmd")

    # write-event
    p = sub.add_parser("write-event", help="写入事件")
    p.add_argument("skill", help="Skill name")
    p.add_argument("--type", required=True, choices=list(EVENT_TYPES), help="Event type")
    p.add_argument("--data", default="{}", help="JSON data")

    # read-events
    p = sub.add_parser("read-events", help="读取事件")
    p.add_argument("skill", help="Skill name")
    p.add_argument("--type", help="Filter by event type")
    p.add_argument("--limit", type=int, default=20)

    # audit-trail
    p = sub.add_parser("audit-trail", help="审计记录完整性")
    p.add_argument("skill", help="Skill name")

    # write-insight
    p = sub.add_parser("write-insight", help="写入洞察")
    p.add_argument("skill", help="Skill name")
    p.add_argument("--text", required=True, help="洞察文本")
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--evidence", default="[]", help="JSON event ID list")

    # read-insights
    p = sub.add_parser("read-insights", help="读取洞察")
    p.add_argument("skill", help="Skill name")

    # global-insights (HyperAgents)
    p = sub.add_parser("global-insights", help="读取跨 skill 全局洞察")
    p.add_argument("--limit", type=int, default=30)

    # write-global-insight (HyperAgents)
    p = sub.add_parser("write-global-insight", help="写入跨 skill 全局洞察")
    p.add_argument("--text", required=True, help="洞察文本")
    p.add_argument("--affected", default="[]", help="JSON 受影响的 skill 列表")
    p.add_argument("--pattern", default="", help="模式名称")
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--source", default="", help="来源 skill")

    # gvu-snr
    p = sub.add_parser("gvu-snr", help="GVU 验证器 SNR 监控")
    p.add_argument("skill", help="Skill name")
    p.add_argument("--lookback", type=int, default=10, help="回溯轮数")

    # fix-quality
    p = sub.add_parser("fix-quality", help="TAPO 修复质量追踪")
    p.add_argument("skill", help="Skill name")
    p.add_argument("--rule", help="指定规则名（不指定则显示全部）")

    # stats
    p = sub.add_parser("stats", help="显示统计信息")
    p.add_argument("skill", help="Skill name")

    args = parser.parse_args()

    if args.cmd == "write-event":
        data = json.loads(args.data)
        fp = write_event(args.skill, args.type, data)
        if fp:
            print(f"[OK] Event written: {fp.name}")
        else:
            print(f"[!!] Skill '{args.skill}' not found")
            sys.exit(1)

    elif args.cmd == "read-events":
        events = read_events(args.skill, args.type, args.limit)
        for e in events:
            print(f"  [{e['event_type']}] {e['iso_timestamp'][:19]} "
                  f"{json.dumps(e['data'], ensure_ascii=False)[:120]}")
        if not events:
            print("  (no events)")

    elif args.cmd == "audit-trail":
        report = audit_trail(args.skill)
        print(f"  总事件数: {report['total_events']}")
        print(f"  修复事件: {report['fix_events']}")
        print(f"  会话数:   {report['sessions']}")
        print(f"  事件类型: {report['event_types']}")
        gvu = report.get("gvu_stability", {})
        if gvu:
            print(f"  GVU SNR:  {gvu.get('verdict', '?')}")
        print(f"  判定:     {report['verdict']}")

    elif args.cmd == "write-insight":
        evidence = json.loads(args.evidence)
        fp = write_insight(args.skill, args.text, evidence, args.confidence)
        if fp:
            print(f"[OK] Insight written: {fp.name}")
        else:
            print(f"[!!] Skill '{args.skill}' not found")
            sys.exit(1)

    elif args.cmd == "read-insights":
        insights = read_insights(args.skill)
        for ins in insights:
            print(f"  [{ins['insight_id']}] conf={ins['confidence']:.0%}")
            print(f"    {ins['text'][:200]}")
        if not insights:
            print("  (no insights)")

    elif args.cmd == "global-insights":
        insights = read_global_insights(limit=args.limit)
        for ins in insights:
            print(f"  [{ins['global_insight_id']}] {ins.get('pattern_name', '?')}")
            print(f"    来源: {ins.get('source_skill', '?')}")
            print(f"    影响: {ins.get('affected_skills', [])}")
            print(f"    应用次数: {ins.get('application_count', 0)}")
            print(f"    {ins.get('text', '')[:150]}")
        if not insights:
            print("  (no global insights)")

    elif args.cmd == "write-global-insight":
        affected = json.loads(args.affected)
        fp = write_global_insight(
            args.text, affected, args.pattern,
            args.confidence, args.source
        )
        if fp:
            print(f"[OK] Global insight written: {fp.name}")
        else:
            print("[!!] Failed to write global insight")
            sys.exit(1)

    elif args.cmd == "gvu-snr":
        result = calculate_gvu_snr(args.skill, args.lookback)
        print(f"  GVU 稳定性分析: {args.skill}")
        print(f"  生成器噪声: {result['generator_noise']}")
        print(f"  验证器 SNR: {result['verifier_snr']}")
        print(f"  稳定性比率: {result['stability_ratio']}")
        print(f"  判定:       {result['verdict']}")

    elif args.cmd == "fix-quality":
        if args.rule:
            quality = get_fix_quality(args.skill, args.rule)
            print(f"  {args.rule}: 成功率={quality['success_rate']:.0%} "
                  f"({quality.get('successes', 0)}/{quality.get('total', 0)})")
        else:
            qualities = get_all_fix_qualities(args.skill)
            if qualities:
                for rule, q in qualities.items():
                    print(f"  {rule}: 成功率={q['success_rate']:.0%} "
                          f"({q.get('successes', 0)}/{q.get('total', 0)})")
            else:
                print("  (no fix quality data)")

    elif args.cmd == "stats":
        report = audit_trail(args.skill)
        print(f"  Skill: {args.skill}")
        print(f"  Events: {report['total_events']} | Sessions: {report['sessions']}")
        print(f"  Types: {report['event_types']}")
        gvu = report.get("gvu_stability", {})
        if gvu:
            print(f"  GVU: {gvu.get('verdict', '?')}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
