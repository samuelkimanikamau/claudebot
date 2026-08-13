"""Parse the newline-delimited JSON that ``claude --output-format stream-json`` emits.

One JSON object per line. The event types we care about (verified live against
claude 2.1.157):

* ``system`` / subtype ``init`` — start of a session; carries ``session_id``, model.
* ``stream_event``               — incremental delta (only with --include-partial-messages).
* ``assistant``                  — a full assistant message (text + tool_use blocks).
* ``user``                       — tool results, and our own turns echoed back
                                   (--replay-user-messages). We ignore these.
* ``result``                     — end of a turn; carries the final ``result`` text,
                                   ``session_id``, ``is_error``, ``total_cost_usd``.
* ``rate_limit_event``           — informational rate-limit status.

The parser is deliberately permissive: unknown shapes return ``None`` or empty
rather than raising, so a protocol tweak in a future claude release degrades
gracefully instead of crashing a turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ClaudeEvent:
    type: str
    raw: dict[str, Any]

    @property
    def subtype(self) -> str | None:
        return self.raw.get("subtype")

    @property
    def session_id(self) -> str | None:
        sid = self.raw.get("session_id")
        return sid if isinstance(sid, str) else None


def parse_line(line: str) -> ClaudeEvent | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return ClaudeEvent(type=str(obj.get("type", "")), raw=obj)


# --- type predicates --------------------------------------------------------

def is_init(e: ClaudeEvent) -> bool:
    return e.type == "system" and e.subtype == "init"


def is_assistant(e: ClaudeEvent) -> bool:
    return e.type == "assistant"


def is_partial(e: ClaudeEvent) -> bool:
    return e.type == "stream_event"


def is_result(e: ClaudeEvent) -> bool:
    return e.type == "result"


def is_compact_boundary(e: ClaudeEvent) -> bool:
    """The conversation was compacted (context squashed into a summary)::

        {"type":"system","subtype":"compact_boundary",
         "compact_metadata":{"trigger":"auto","pre_tokens":1000384}}
    """
    return e.type == "system" and e.subtype == "compact_boundary"


def compact_trigger(e: ClaudeEvent) -> str:
    meta = e.raw.get("compact_metadata")
    if isinstance(meta, dict):
        return str(meta.get("trigger", "auto"))
    return "auto"


# --- field extractors -------------------------------------------------------

def assistant_text(e: ClaudeEvent) -> str:
    """Concatenate the text blocks of an assistant message event."""
    content = ((e.raw.get("message") or {}).get("content")) or []
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def assistant_tool_names(e: ClaudeEvent) -> list[str]:
    """Names of any tools the assistant invoked in this message event."""
    content = ((e.raw.get("message") or {}).get("content")) or []
    names: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            names.append(str(block.get("name", "tool")))
    return names


def partial_text(e: ClaudeEvent) -> str | None:
    """Incremental text delta from a ``stream_event`` (partial-messages mode).

    Mirrors the Anthropic Messages streaming shape, wrapped under ``event``::

        {"type":"stream_event","event":{"type":"content_block_delta",
          "delta":{"type":"text_delta","text":"..."}}}
    """
    ev = e.raw.get("event")
    if not isinstance(ev, dict) or ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "text_delta":
        return str(delta.get("text", ""))
    return None


def partial_thinking(e: ClaudeEvent) -> str | None:
    """Incremental extended-thinking delta from a ``stream_event``::

        {"type":"stream_event","event":{"type":"content_block_delta",
          "delta":{"type":"thinking_delta","thinking":"..."}}}
    """
    ev = e.raw.get("event")
    if not isinstance(ev, dict) or ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
        return str(delta.get("thinking", ""))
    return None


def result_text(e: ClaudeEvent) -> str:
    r = e.raw.get("result")
    return r if isinstance(r, str) else ""


def result_is_error(e: ClaudeEvent) -> bool:
    if e.raw.get("is_error"):
        return True
    return e.subtype not in (None, "success")


def result_cost(e: ClaudeEvent) -> float | None:
    c = e.raw.get("total_cost_usd")
    return float(c) if isinstance(c, (int, float)) else None
