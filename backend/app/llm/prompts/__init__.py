"""Prompts système figés par agent — condition du prefix caching (ADR-003)."""

from app.llm.prompts.registry import (
    AgentName,
    all_agents,
    get_system_prompt,
    prompt_version,
)

__all__ = ["AgentName", "all_agents", "get_system_prompt", "prompt_version"]
