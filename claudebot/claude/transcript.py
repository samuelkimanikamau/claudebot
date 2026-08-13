"""Read-only helpers over Claude Code's on-disk session transcripts.

Claude Code stores each session as ``~/.claude/projects/<encoded-cwd>/<id>.jsonl``,
where the cwd is encoded by replacing every non-alphanumeric character with ``-``
(verified against real project dirs: ``/a/b.c_d`` -> ``-a-b-c-d``). The transcript
grows for the life of the conversation and records a line with
``"isCompactSummary":true`` each time the context is compacted — which is when
older conversation details get squashed into a summary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TranscriptStats:
    path: Path
    size_bytes: int
    compactions: int
    last_compact_at: str | None  # ISO timestamp of the newest compaction, if any


def project_dir(working_dir: Path | str) -> Path:
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(working_dir))
    return Path.home() / ".claude" / "projects" / encoded


def transcript_path(working_dir: Path | str, session_id: str) -> Path:
    return project_dir(working_dir) / f"{session_id}.jsonl"


def transcript_stats(working_dir: Path | str, session_id: str) -> TranscriptStats | None:
    """Size + compaction history of a session's transcript, or None if absent.

    Scans the file line-wise (a very long conversation can be >100 MB); call it
    off the event loop, e.g. via ``asyncio.to_thread``.
    """
    path = transcript_path(working_dir, session_id)
    try:
        size = path.stat().st_size
    except OSError:
        return None
    compactions = 0
    last_line: bytes | None = None
    try:
        with path.open("rb") as fh:
            for line in fh:
                if b'"isCompactSummary"' in line:
                    compactions += 1
                    last_line = line
    except OSError:
        pass
    return TranscriptStats(
        path=path,
        size_bytes=size,
        compactions=compactions,
        last_compact_at=_timestamp(last_line),
    )


def _timestamp(line: bytes | None) -> str | None:
    if line is None:
        return None
    try:
        ts = json.loads(line).get("timestamp")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return None
    return str(ts) if ts else None
