"""Allow ``python -m claudebot`` as an alias for the ``claudebot`` console script."""

from claudebot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
