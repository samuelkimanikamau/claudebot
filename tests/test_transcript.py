"""Transcript location + compaction stats (context-health data for /status)."""

from __future__ import annotations

import json
from pathlib import Path

from claudebot.claude.transcript import project_dir, transcript_stats


def test_project_dir_encodes_every_non_alphanumeric_as_dash():
    # Verified against real ~/.claude/projects entries:
    #   /Users/x/Vutia.Enterprises/Claudebot -> -Users-x-Vutia-Enterprises-Claudebot
    #   /Users/x/Homeboyz/tikiti_agent       -> -Users-x-Homeboyz-tikiti-agent
    assert project_dir("/Users/x/Vutia.Enterprises/Claudebot").name == (
        "-Users-x-Vutia-Enterprises-Claudebot"
    )
    assert project_dir("/Users/x/Homeboyz/tikiti_agent").name == (
        "-Users-x-Homeboyz-tikiti-agent"
    )


def test_transcript_stats_counts_compactions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pdir = tmp_path / ".claude" / "projects" / "-home-sam"
    pdir.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {}},
        {"isCompactSummary": True, "timestamp": "2026-08-10T09:00:00.000Z"},
        {"type": "assistant", "message": {}},
        {"isCompactSummary": True, "timestamp": "2026-08-13T07:30:00.000Z"},
    ]
    f = pdir / "abc-123.jsonl"
    f.write_text("\n".join(json.dumps(line) for line in lines) + "\n", "utf-8")

    stats = transcript_stats("/home/sam", "abc-123")
    assert stats is not None
    assert stats.size_bytes == f.stat().st_size
    assert stats.compactions == 2
    assert stats.last_compact_at == "2026-08-13T07:30:00.000Z"


def test_transcript_stats_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert transcript_stats("/home/sam", "no-such-session") is None
