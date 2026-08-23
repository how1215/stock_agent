# Contributing

Thanks for taking an interest in Taiwan Stock Market Agent. This repository keeps model-dependent behaviour separate from deterministic market-data logic so that most changes can be reviewed and tested without a GPU.

## Development setup

Requirements: Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/how1215/stock_agent.git
cd stock_agent
uv sync --all-groups
cp .env.example .env
```

The `.env` file is optional for data-only commands and tests. A running vLLM server is required only for agent queries, descriptive scans, briefing generation, and behavioural evals.

## Quality checks

Run these before opening a pull request:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The test suite uses recorded TWSE fixtures and must not depend on the network or a live model. If a change affects prompt wording, tool descriptions, or routing behaviour, also run:

```bash
uv run python evals/run_evals.py
```

## Design constraints

- Keep arithmetic and unit conversion in deterministic Python. The LLM may interpret numbers but must not calculate source-of-truth values.
- Preserve the JSON CLI contract: stdout contains one JSON payload, diagnostics go to stderr, and failures exit non-zero with an `error` field.
- Every market-data response must state the actual trading date returned by TWSE or the local cache.
- Requested historical dates must never fall back to a different day's latest data.
- Prefer ordinary functions for linear deterministic work. Use LangGraph where branching, fan-out, joins, or state reduction materially help.
- Keep the Pi extension thin; shared formatting belongs in `stock_agent/render.py`.

Architecture details live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), while accepted trade-offs and rejected alternatives are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).

## Pull requests

Keep changes focused, explain the user-visible behaviour, and include tests for deterministic logic. Do not commit `.env`, cache files, generated reports, traces, logs, or local watchlists.
