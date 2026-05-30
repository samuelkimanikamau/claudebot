"""claudebot — an always-on Telegram bot that drives the real Claude Code binary.

It talks to the genuine ``claude`` executable over its headless ``stream-json``
protocol on your logged-in Pro/Max subscription. No API key, no Agent SDK — the
OAuth token never leaves Claude Code, which keeps the setup inside Anthropic's
supported usage (see README "Is this allowed?").
"""

__version__ = "0.1.0"
