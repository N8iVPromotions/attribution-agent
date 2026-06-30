"""
utils/prompt_loader.py
----------------------
Loads versioned agent prompt definitions from .claude/agents/*.md.

Parses YAML frontmatter (name, version, model, updated_at) and returns
the body separately. The active git prompt tag is read from the
GIT_PROMPT_TAG env var (set by deploy workflow).
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PromptDefinition:
    name: str
    version: str
    body: str
    git_tag: str
    model: str = ""
    updated_at: str = ""


def _find_agents_dir() -> Path:
    if env_path := os.environ.get("N8IV_AGENTS_DIR"):
        return Path(env_path)
    candidate = Path(__file__).resolve()
    for _ in range(8):
        candidate = candidate.parent
        agents_dir = candidate / ".claude" / "agents"
        if agents_dir.is_dir():
            return agents_dir
    return Path.home() / ".claude" / "agents"


class PromptLoader:
    def __init__(self) -> None:
        self._agents_dir = _find_agents_dir()
        self._git_tag = os.environ.get("GIT_PROMPT_TAG", "dev")

    def load(self, agent_name: str) -> PromptDefinition:
        path = self._agents_dir / f"{agent_name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Agent definition not found: {path}")

        content = path.read_text(encoding="utf-8")
        frontmatter: dict[str, str] = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        key, _, val = line.partition(":")
                        frontmatter[key.strip()] = val.strip().strip('"').strip("'")
                body = parts[2].strip()

        return PromptDefinition(
            name=frontmatter.get("name", agent_name),
            version=frontmatter.get("version", "1.0.0"),
            body=body,
            git_tag=self._git_tag,
            model=frontmatter.get("model", ""),
            updated_at=frontmatter.get("updated_at", ""),
        )
