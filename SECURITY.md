# Security policy

## Supported version

This is a pre-1.0 personal project. Security fixes are applied to the latest commit on `main`; older revisions are not maintained.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or exposed credential. Use GitHub's private vulnerability reporting from the repository's **Security** tab. Include reproduction steps, affected files or commands, and the potential impact when possible.

If private vulnerability reporting is unavailable, open a minimal issue asking the maintainer for a private contact channel without including exploit details.

## Scope and operational notes

- The application reads public TWSE data and does not require a TWSE API key.
- The configured vLLM endpoint is trusted as part of the local deployment boundary.
- The optional Pi extension launches only this repository's fixed Python CLI entry point with structured arguments.
- Runtime files may contain a personal watchlist, prompts, model output, or tool arguments. The `data/`, `reports/`, `traces/`, and `logs/` directories are intentionally excluded from version control.
- This software provides market-data summaries, not investment advice.
