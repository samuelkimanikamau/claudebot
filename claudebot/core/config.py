"""Typed configuration loaded from ``~/.claudebot/.env`` and the environment.

Every field can be set as ``CLAUDEBOT_<UPPER_SNAKE>`` in the environment or the
``.env`` file (env wins). List fields accept a comma-separated string
(``CLAUDEBOT_ALLOWED_USER_IDS=111,222``) — we disable pydantic-settings' default
JSON decoding for them with ``NoDecode`` so a plain CSV just works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from claudebot.core.paths import env_file

# Permission modes the real `claude` binary accepts in headless mode.
PERMISSION_MODES = ("bypassPermissions", "acceptEdits", "default", "plan", "dontAsk")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAUDEBOT_",
        env_file=str(env_file()),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram -----------------------------------------------------------
    telegram_bot_token: SecretStr = Field(
        ..., description="Bot token from @BotFather."
    )
    allowed_user_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        description="Telegram numeric user IDs allowed to use the bot. "
        "Empty means NO ONE (locked) — set at least your own ID.",
    )

    # --- Claude Code --------------------------------------------------------
    claude_binary: str = Field(
        default="claude", description="Path to the `claude` executable."
    )
    working_dir: Path = Field(
        default_factory=Path.home,
        description="Directory Claude runs in (its --add-dir / cwd).",
    )
    model: str | None = Field(
        default=None, description="Model alias or full name, e.g. 'opus' / 'sonnet'."
    )
    permission_mode: str = Field(
        default="bypassPermissions",
        description="Headless permission mode. bypassPermissions never blocks "
        "(personal trusted machine); acceptEdits is safer; default/plan may stall "
        "a turn since there is no interactive prompt.",
    )
    effort: str | None = Field(
        default=None, description="Effort level: low/medium/high/xhigh/max."
    )
    append_system_prompt: str | None = Field(
        default=None, description="Extra system prompt appended to Claude's default."
    )
    system_prompt_file: Path | None = Field(
        default=None, description="File whose contents are appended to the system prompt."
    )
    allowed_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    disallowed_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Behaviour ----------------------------------------------------------
    stream_partials: bool = Field(
        default=True, description="Stream Claude's reply into Telegram as it types."
    )
    markdown: bool = Field(
        default=True,
        description="Render Claude's Markdown as Telegram formatting (bold, code, "
        "tables→monospace). Falls back to plain text if a message won't parse.",
    )
    edit_interval: float = Field(
        default=1.3, description="Minimum seconds between streaming message edits."
    )
    idle_timeout: int = Field(
        default=3600,
        description="Kill a chat's idle claude child after N seconds (0 = never). "
        "The session_id is kept, so the next message resumes context.",
    )
    turn_timeout: int = Field(
        default=1800,
        description="Abort a turn that produces no result after N seconds (0 = no limit). "
        "Prevents a hung tool from wedging the chat; the session resets and resumes.",
    )
    safety_preamble: bool = Field(
        default=True,
        description="Append a system-prompt instruction to treat attached/forwarded/"
        "fetched content as untrusted data, not instructions (content-injection guard).",
    )
    show_cost: bool = Field(
        default=False, description="Append a token/cost footer after each reply."
    )
    log_level: str = Field(default="INFO")

    # --- validators ---------------------------------------------------------
    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        return _split_csv(v)

    @field_validator("allowed_tools", "disallowed_tools", mode="before")
    @classmethod
    def _split_tools(cls, v: object) -> object:
        return _split_csv(v)

    @field_validator("permission_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in PERMISSION_MODES:
            raise ValueError(
                f"permission_mode must be one of {PERMISSION_MODES}, got {v!r}"
            )
        return v

    @field_validator("working_dir", "system_prompt_file", mode="before")
    @classmethod
    def _expand(cls, v: object) -> object:
        if v in (None, ""):
            return None if v == "" else v
        return Path(str(v)).expanduser()


def _split_csv(v: object) -> object:
    """Turn a comma-separated string into a list; pass lists through."""
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        return [part.strip() for part in v.split(",") if part.strip()]
    return v


def load_settings() -> Settings:
    """Construct Settings, surfacing a friendly hint when the token is missing."""
    return Settings()  # type: ignore[call-arg]
