#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Platform abstraction layer — single entry point for all platform-specific config.

This module is the ONLY place where paths, env vars, and LLM API details are
hardcoded.  Every other module imports from here instead of baking its own
copy of _find_skill_root() or calling os.environ directly.

Porting skill-builder-v3 to another agent platform requires changing ONLY this file.
Everything else is pure Python stdlib.

Environment variable reference:

  LLM_API_KEY          Primary API key (any provider)
  LLM_BASE_URL         Primary base URL (any provider)
  LLM_MODEL            Primary model name (any provider)
  ── fallbacks ──
  ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
  ANTHROPIC_BASE_URL
  ANTHROPIC_MODEL

  SB3_SKILLS_DIR       Override skills base directory
                        (default: ~/.claude/skills  ← Claude Code convention)
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Directory layout
# ═══════════════════════════════════════════════════════════════════════════════

SKILLS_BASE = Path(
    os.environ.get("SB3_SKILLS_DIR",
                   str(Path.home() / ".claude" / "skills"))
)


def get_skills_dir() -> Path:
    """Root directory containing all skill folders."""
    return SKILLS_BASE


def get_config_dir() -> Path:
    """Agent config directory (parent of skills dir)."""
    return SKILLS_BASE.parent


def get_data_dir(skill_name: str) -> Optional[Path]:
    """data/ directory inside a skill."""
    root = find_skill_root(skill_name)
    if not root:
        return None
    return root / "data"


def find_skill_root(skill_name: str) -> Optional[Path]:
    """Find a skill's root directory by name (case-insensitive).

    This replaces the 15+ copies of _find_skill_root() that were
    scattered across the codebase.
    """
    base = get_skills_dir()
    if not base.is_dir():
        return None
    for d in base.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            return d
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LLM configuration
# ═══════════════════════════════════════════════════════════════════════════════

def get_llm_config() -> dict:
    """Return LLM credentials and endpoint.

    Reads standardised env vars first, falls back to Anthropic-prefixed
    vars (Claude Code convention).  Any agent platform can set the
    standard vars to override.
    """
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )
    base_url = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    )
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    )
    return {"api_key": api_key, "base_url": base_url, "model": model}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Unified LLM call (Anthropic Messages API — compatible with DeepSeek & others)
# ═══════════════════════════════════════════════════════════════════════════════

def call_llm(system_prompt: str, user_prompt: str,
             max_tokens: int = 4096, timeout: int = 120) -> tuple:
    """Call LLM via Anthropic-compatible Messages API.

    Returns (success: bool, text: str, error: str).

    Works with any provider that exposes an Anthropic-compatible
    /v1/messages endpoint (Anthropic, DeepSeek, OpenRouter, etc.).
    """
    config = get_llm_config()
    if not config["api_key"]:
        return False, "", "LLM_API_KEY or ANTHROPIC_AUTH_TOKEN not set"

    api_url = config["base_url"].rstrip("/") + "/v1/messages"

    body = json.dumps({
        "model": config["model"],
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))

        # Find the 'text' block (thinking models return 'thinking' first)
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block["text"]
                break
        if not text:
            text = data["content"][0].get("text", "")
        if not text:
            return False, "", f"API returned no text: {json.dumps(data)[:200]}"
        return True, text, ""

    except Exception as e:
        return False, "", str(e)[:200]
