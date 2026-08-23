# Taiwan Stock Market Agent

[![CI](https://github.com/how1215/stock_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/how1215/stock_agent/actions/workflows/ci.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A pre-market assistant for Taiwan Stock Exchange (TWSE) data, built as a **LangGraph agent system** running against a **locally-hosted LLM via vLLM**. It does two things: answers ad-hoc questions in natural language, and generates a scheduled daily briefing over a personal watchlist. Every number in every answer traces back to a deterministic Python computation.

The application owns the agent loop — its own tools, state, checkpointing, tracing, and evaluation harness — rather than delegating it to an off-the-shelf coding harness. [The reasoning behind that choice is documented.](docs/DECISIONS.md#1-the-application-owns-the-agent-loop-not-a-coding-harness)

Three LangGraph shapes are used, each because a simpler construct wouldn't do the job: a **prebuilt ReAct loop** for question answering, a **dynamic `Send` fan-out** for per-stock judgement across a screened candidate set, and a **static parallel join** for the daily briefing that composes the fan-out graph as a subgraph.

[Architecture](docs/ARCHITECTURE.md) · [Engineering decisions](docs/DECISIONS.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

```
$ uv run python -m stock_agent.cli ask "台積電和鴻海今天收多少？" --trace
  → get_stock_quote({'codes': ['台積電', '鴻海']})
  ← get_stock_quote (6.9 ms, 247 chars)

2026-08-03 收盤數據如下：

*   **台積電 (2330)**：收盤價 2,370.00 元，跌 55.00 元 (-2.27%)。
*   **鴻海 (2317)**：收盤價 253.00 元，漲 2.50 元 (+1.00%)。

以上為公開數據整理，非投資建議。
```

Note what the model did here: it passed *company names* rather than guessing ticker symbols, and it batched both stocks into a single tool call. Both behaviours are enforced by tool design and locked in by the eval suite.

---

## Contents

- [Architecture](#architecture)
- [Design highlights](#design-highlights)
- [Quickstart](#quickstart)
- [Daily pre-market briefing](#daily-pre-market-briefing)
- [Optional: Pi extension](#optional-pi-extension)
- [Data source and its limitations](#data-source-and-its-limitations)
- [Testing and evaluation](#testing-and-evaluation)
- [Observability](#observability)
- [Project structure](#project-structure)
- [Not implemented](#not-implemented)
- [Contributing and licence](#contributing-and-licence)

---

## Architecture

```
                     user question
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  ReAct agent (LangGraph)             │
        │  create_react_agent + InMemorySaver  │
        │  ChatOpenAI ──► local vLLM           │
        └──────────────────┬───────────────────┘
                           │ model selects tools
                           ▼
        ┌──────────────────────────────────────┐
        │  tools.py    6 tools, LLM-facing     │
        └────────┬─────────────────────┬───────┘
                 │                     │ scan_market / get_briefing
                 │                     ▼
                 │   ┌────────────────────────────────────────┐
                 │   │  workflow/                             │
                 │   │                                        │
                 │   │  briefing graph  (static parallel join) │
                 │   │    snapshot ─┐                          │
                 │   │    watchlist ─┼─► compose ─► persist    │
                 │   │    movers ───┘      │                   │
                 │   │        └─ composes ─┘                   │
                 │   │  scan graph  (dynamic Send fan-out)     │
                 │   │    select ─► [Send × N] ─► aggregate    │
                 │   └──────────────────┬─────────────────────┘
                 ▼                      │
        ┌──────────────────────────────────────┐
        │  query.py    structured results      │──► JSON CLI ──► Pi extension
        └──────────┬───────────────┬───────────┘                 (optional)
                   ▼               ▼
            screen.py         render.py         watchlist.py
      (filter/sort/breadth)  (formatting)     (personal list)
                   │
                   ▼
        ┌──────────────────────────────────────┐
        │  sources/  cache ──► TWSE open API   │
        └──────────────────────────────────────┘
```

Four of the six tools are simple lookups. Two mount hand-written graphs:

- **`scan_market`** fans out one LLM call per candidate stock via `Send` and reduces the results — a shape the prebuilt ReAct loop structurally cannot express. [Why it earns its place, with measurements.](docs/DECISIONS.md#11-a-hand-written-stategraph-for-per-stock-fan-out)
- **`get_briefing`** runs three independent branches in parallel and joins them, composing the scan graph as a **subgraph** to read the watchlist. [Details.](docs/ARCHITECTURE.md#the-briefing-graph-static-parallelism-and-subgraph-composition)

Two properties matter more than the box diagram:

**The LLM never does arithmetic.** Percentage change, lot conversion, and 億元 scaling are all computed in Python and rendered to text before the model sees them. The system prompt forbids the model from producing any number that did not come from a tool result, and an eval case asserts it doesn't.

**`render.py` is shared, deliberately.** Both the Python agent's tools and the JSON CLI's `text` field render through it, so the text a local model sees and the text an external caller receives are byte-identical. A test asserts this equality — without it, two presentation implementations would drift.

Full data flow and layer responsibilities: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Design highlights

| | |
|---|---|
| **Tool interface as the contract** | Tool descriptions are the model's only basis for choosing a tool, so they carry the usage rules. Errors return *actionable* text (`"代號 9999 不存在。上市普通股代號為 4 位數字、首碼 1–9…"`) rather than exceptions, letting the model self-correct instead of dead-ending. |
| **Two-tier verification** | `pytest` covers deterministic logic against recorded API fixtures — no network, no LLM, typically under a second. A separate eval harness covers agent *behaviour*: did it pick the right tool, with the right arguments, without inventing numbers? |
| **Evals that don't rot** | Expected values are placeholders (`{taiex_close}`, `{close:2330}`) resolved against the data layer at run time, so cases stay valid as market data changes daily. |
| **Honest failure over plausible answers** | The upstream endpoint serves only the latest trading day. A request for a date not in the local cache returns an explicit error instead of silently substituting today's numbers — a wrong-date answer is worse than a refusal. |
| **Fail-loud dependency check** | vLLM silently ignores tool definitions unless launched with the right flags; `bind_tools` does *not* raise. `cli verify` turns that into an explicit pass/fail. |
| **Fan-out with failure isolation** | The scan graph runs one LLM call per stock in parallel. A single branch that errors or returns unparseable output is recorded as a failure and the rest of the batch completes — no truncation, no cross-contamination between stocks. Measured 1.85× faster than serial on a compute-bound local model. |
| **Expensive work stays out of the hot path** | The briefing takes ~80 s to generate; interactive reads hit the saved file in 0 ms. Re-runs on a day with no new trading data detect that and leave the existing report untouched, so `--refresh` is safe to invoke casually. Optional launchd scheduling moves the cost to 08:30 — with a [macOS caveat](docs/DECISIONS.md#13-scheduling-on-the-os-offered-but-not-default). |

Trade-offs and rejected alternatives: [docs/DECISIONS.md](docs/DECISIONS.md).

## Quickstart

**Prerequisites:** Python 3.13+ and [uv](https://docs.astral.sh/uv/). A vLLM server is only required for natural-language agent queries, descriptive scans, and briefing generation; the data-only CLI and deterministic test suite run without a model or GPU.

```bash
git clone https://github.com/how1215/stock_agent.git
cd stock_agent
uv sync --all-groups

# No model required: fetch the latest TWSE close as JSON
uv run python -m stock_agent.cli market

# No network or model required: runs against recorded fixtures
uv run pytest
```

For the full agent, copy `.env.example` to `.env` and point it at an OpenAI-compatible vLLM endpoint. vLLM **must** be started with tool calling enabled, or the ReAct loop cannot work:

```bash
--enable-auto-tool-choice --tool-call-parser <parser>
```

The parser depends on the model (`hermes` for Qwen-family, `llama3_json` for Llama 3, `mistral` for Mistral). Without these flags `bind_tools` raises no error — the model simply never emits a tool call, which is the hardest failure mode to diagnose. Verify explicitly:

```bash
uv run python -m stock_agent.cli verify
# → 通過：tool calling 正常：[{'name': 'get_stock_quote', 'args': {'codes': ['台積電']}, …}]
```

Then:

```bash
# Interactive session — follow-up questions share context via the checkpointer
uv run python -m stock_agent.cli

# One-shot
uv run python -m stock_agent.cli ask "今天漲幅前 10 名是哪些股票？"

# Show which tools were called, with latencies
uv run python -m stock_agent.cli ask "台積電收多少？" --trace

# Run the scan graph directly, bypassing the ReAct layer (useful for latency work)
uv run python -m stock_agent.cli scan "收盤收在當日最高附近、買盤強勢" --limit 16
```

Configuration is via `.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `VLLM_BASE_URL` | `http://127.0.0.1:8000/v1` | OpenAI-compatible endpoint |
| `VLLM_MODEL` | `local-llm` | Served model name |
| `CACHE_DIR` | `./data` | Trading-day cache |
| `TRACE_DIR` | `./traces` | Execution traces |
| `REPORTS_DIR` | `./reports` | Generated briefings |
| `WATCHLIST_PATH` | `./data/watchlist.json` | Personal watchlist |

### Data-only mode (no LLM)

The same CLI exposes the data layer as JSON. Useful for scripting, and it is what the Pi extension calls.

```bash
uv run python -m stock_agent.cli market
uv run python -m stock_agent.cli quote 2330 鴻海
uv run python -m stock_agent.cli screen --sort-by volume --limit 10
uv run python -m stock_agent.cli watchlist                    # list / add / remove / clear
```

Contract: **stdout is JSON only**, logs go to stderr, and failures exit non-zero with `{"error": "..."}` on stdout. Every payload carries `trade_date` and a pre-rendered `text` field. The contract is covered by tests, not just documented.

## Daily pre-market briefing

The briefing covers the index, market breadth, a per-stock reading of your watchlist, and market-wide movers. Generate it once per trading day; subsequent reads come from disk.

```bash
uv run python -m stock_agent.cli watchlist add 2330 聯發科 3231   # codes or company names
uv run python -m stock_agent.cli briefing --refresh               # generate (~80s)
uv run python -m stock_agent.cli briefing                        # read today's saved report (instant)
```

Optionally schedule it for weekday mornings so it is already on disk when you sit down:

```bash
./scripts/install-launchd.sh install    # weekdays 08:30 via launchd
./scripts/install-launchd.sh run        # trigger once, for testing
```

**macOS caveat:** launchd cannot execute anything inside `~/Documents`, `~/Desktop`, or `~/Downloads` — those are TCC-protected, and while your Terminal is authorised, the launchd context is not. The job fails with `Operation not permitted`, visible only in the log. `install` detects this and refuses with instructions rather than registering a job that silently never works. Either keep the project outside those directories, or generate on demand.

Generating on demand costs one thing worth knowing: **history only accumulates on days you actually use the assistant.** The upstream endpoint has no date parameter, so a trading day never fetched is lost permanently. Any query writes that day's file as a side effect, so regular use mostly covers it — but gaps are possible, and they are what stands between this project and moving-average work.

Excerpt from a real run:

```markdown
# 盤前提要 2026-08-05（依據 2026-08-03 收盤）

## 大盤
發行量加權股價指數 43,386.41（266.66 點，+0.62%）
上漲 651 家、下跌 354 家、平盤 78 家（共 1083 檔普通股）
漲停 53 檔、跌停 0 檔

## 自選股（4 檔）
_判斷軸：昨日收盤是否呈現買盤佔優：收紅、收盤位置偏高、且沒有明顯的開高走低_

・ 2317 鴻海｜符合｜收盤漲幅+1.00%收紅，收盤位置91偏高，開盤244.50後一路走高。
・ 2330 台積電｜不符合｜漲跌幅-2.27%收黑，且收盤位置17偏低。
・ 2454 聯發科｜符合｜收紅且收盤位置100為最高位，開盤即最高無開高走低現象。
```

Four details in there are load-bearing:

**Two different dates.** The filename and title use the **report date** (today); the body states the **trading date** it is based on (the previous close). Pre-market means yesterday's data — conflating the two would misrepresent when the numbers are from. The header is written deterministically, never by the model.

**The judgement axis is printed.** `符合` is meaningless unless you know what was judged. An early version asked "is this worth watching today" — every stock came back `符合`, because a weak stock is also worth watching. The axis was narrowed to buying-pressure so that `不符合` (as 台積電 above) carries information.

**Reads from disk by default.** Generation takes ~80 s; waiting for that on every question would make the assistant unusable. `--refresh` is opt-in, and the scheduled job is what normally produces the file.

**Re-running on a day with no new data doesn't overwrite.** If the newest available trading day matches what the existing report was already based on, the run reports `skipped` and leaves the file alone — verified at 0.6 s versus 80 s for a real generation. This matters for weekend and holiday runs under a scheduler, and it also means `--refresh` is safe to invoke casually.

## Optional: Pi extension

[Pi](https://github.com/earendil-works/pi) is a terminal agent harness. `.pi/extensions/tw-stock.ts` registers six typed tools (`stock_briefing`, `stock_watchlist`, `stock_market`, `stock_quote`, `stock_screen`, `stock_scan`) that shell out to the JSON CLI above, so the assistant is usable from a Pi session.

```bash
pi -e .pi/extensions/tw-stock.ts
```

Pi is the intended day-to-day driver, but it is still a **thin shell, not a dependency** — it holds no application logic beyond argument marshalling, and removing the extension leaves the system fully usable through the CLI. Pi has no MCP support by design, so a CLI-backed extension is the supported integration path. [Why using it as the interface doesn't contradict the "own your agent loop" decision.](docs/DECISIONS.md#10-pi-as-the-day-to-day-interface-still-holding-no-logic)

The extension deliberately does no number formatting of its own: it forwards the `text` field from the JSON payload. Reimplementing the formatting rules in TypeScript would guarantee eventual drift.

### The daily routine as a skill

`.pi/skills/premarket/SKILL.md` packages the whole morning routine, so a trading day is one command:

```
/skill:premarket
```

It sequences existing tools — no new capability — and the ordering carries a reason the model would otherwise optimise away: **step 1 must call `stock_market` before reading the briefing.** A cached briefing read performs zero HTTP requests (measured) and therefore never writes that day's cache file. Since the upstream endpoint has no date parameter, a trading day not fetched is lost permanently, so the routine's first step is what keeps history growing.

The rest of the skill is an output template and the reporting discipline: state the trading date rather than "today", state the judgement axis before quoting a `符合`/`不符合` verdict, quote numbers only from tool output, no investment advice.

**How it's evaluated.** A skill is prose for a model, so the Python eval harness cannot drive it. What *is* automated (`tests/test_pi_assets.py`, 12 tests) is reference integrity, which catches a real class of silent failure:

- every `stock_*` name in `SKILL.md` and `AGENTS.md` resolves to a tool the extension actually registers
- every CLI subcommand the extension invokes exists in `cli.py`
- every registered tool is documented in `AGENTS.md`
- the skill states the step-1 constraint and warns about `refresh` cost
- all three model-facing documents mention the trading-date distinction

A skill telling the model to call `stock_levels` would fail silently at runtime — the model just picks a different tool. These tests were mutation-checked: injecting a bad tool name makes them fail.

What still needs a live Pi session: whether the model follows the ordering, and whether its condensed output is actually useful. That is a manual checklist, not a suite — and it is the honest cost of putting a routine in a harness rather than in code.

### Other notes

One asymmetry is intentional: the extension can `add` and `remove` watchlist entries, but there is no `clear`. Wiping the whole list is destructive and a human is watching a Pi session, so add/remove is reasonable there — but the risk of a model doing it on a misread instruction isn't worth the convenience. `clear` lives in the CLI only, and the Python ReAct agent's watchlist tool is read-only for the same reason.

> Status: the CLI contract the extension depends on is covered by tests, and the TypeScript passes a syntax check. End-to-end verification inside a live Pi session has not been run — `pi` was not installed in the development environment.

## Data source and its limitations

| Endpoint | Content |
|---|---|
| `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | Daily OHLCV for all listed stocks, whole market in one response |
| `.../exchangeReport/MI_INDEX` | Daily closing values for all TWSE indices |

No API key required. Measured: one request, 318 KB, 0.15 s, 1,377 rows (1,086 of them common stocks).

Three limitations shape the entire design:

1. **Listed (上市) only** — over-the-counter (上櫃/TPEx) is a separate API and is out of scope. The agent says so when asked.
2. **End-of-day data, with a publication lag longer than you'd expect.** The endpoint returns the **previous** trading day for most of the day — observed still serving 8/3 at 20:50 on Tuesday 8/4, over seven hours after the 13:30 close. Today's close appears later, in practice by the next morning. The agent therefore always states the actual trading date rather than saying "today", and a question like "what did the index close at today?" correctly answers with the previous session.
3. **The endpoints accept no date parameter.** They always return the latest trading day — the Swagger spec declares no query parameters, and passing `?date=` is ignored (verified). Historical data can only accumulate through daily fetches into the local cache; a date that isn't cached returns an error rather than a substituted answer.

### Data-handling details worth knowing

Each of these was verified against live responses, and each is a plausible way to get the numbers wrong:

- **Republic-of-China calendar dates.** `"1150803"` → `2026-08-03` (`year + 1911`).
- **Percentage change must be derived.** The API gives `Change` (absolute delta, correctly signed) but no percentage. `prev_close = close - change`; `change_pct = change / prev_close * 100`. Verified against 2330: close 2370, change -55 → prev close 2425, -2.27%, matching the per-stock endpoint.
- **Index sign lives in a separate field.** `漲跌點數` is always unsigned; direction is in `漲跌` (`+`/`-`). `漲跌百分比` is signed. Checked across all 267 index rows: zero sign disagreements.
- **Common-stock codes are 4 digits with a leading 1–9.** Checking only "4 digits" misclassifies ETFs — `0050`, `0056` and 6 others are 4-digit numeric but are ETFs. This was caught by a test, not by inspection.
- **Empty strings on halted stocks.** Price fields can be `""`; they must become `None`, not `float("")`.

## Testing and evaluation

```bash
uv run pytest                      # 172 tests, ~0.7s, no network, no LLM
uv run ruff check .                # import and static lint checks
uv run ruff format --check .       # formatting contract used by CI
uv run python evals/run_evals.py   # 12 behavioural cases, requires vLLM
```

The two layers answer different questions.

**`pytest` — is the deterministic logic correct?** Runs against recorded real API responses in `tests/fixtures/`, including a hand-added halted-stock row that the live data didn't contain that day. Covers date conversion, sign handling, empty-value defence, cache-hit behaviour (asserting no HTTP call is made), filtering and sorting, the tool text layer, and the JSON CLI contract including exit codes.

The scan graph is covered here too, with a fake model injected — `build_scan_graph(llm)` takes the model as a parameter precisely so that fan-out branch counts, output parsing, and failure isolation are testable without a GPU.

**`evals/` — does the agent behave?** Each case declares the tools that must be called, argument subsets that must match, strings that must appear, and strings that must **not** appear:

```yaml
- name: 不存在的代號不得編造數字
  question: 9999 這檔股票今天收多少？
  expect_tools: [get_stock_quote]
  expect_contains_any: ["查不到", "沒有", "找不到", "不存在"]
  forbid_contains: ["{taiex_close}"]
```

Several cases guard tool *selection* rather than output. `scan_market` is an order of magnitude slower than `screen_stocks`, so one case asserts it is used for a descriptive condition and another asserts it is **not** used for a purely numeric one. Likewise `forbid_tools: [screen_stocks]` on "what's in my watchlist?" — a market-wide screen cannot substitute for a user's personal list, and a model that reaches for it has misunderstood the question.

This is regression testing for prompt and tool-description changes: swap the model or reword a description, run the suite, and see immediately whether behaviour degraded. Current state: 12/12 passing on `gemma-4-31B-it-qat-w4a16-ct`.

## Observability

Every run appends to `traces/<thread_id>.jsonl`, one event per line — LLM token counts and latency, tool arguments and durations, errors:

```jsonl
{"ts":"2026-08-04T07:32:41.967Z","event":"llm_end","latency_ms":7048.8,"prompt_tokens":1171,"completion_tokens":34,"total_tokens":1205}
{"ts":"2026-08-04T07:32:41.969Z","event":"tool_start","tool":"get_stock_quote","args":{"codes":["台積電"],"date":"2020-01-02"}}
{"ts":"2026-08-04T07:32:41.971Z","event":"tool_end","tool":"get_stock_quote","latency_ms":1.4,"output_chars":70}
{"ts":"2026-08-04T07:32:51.811Z","event":"llm_end","latency_ms":9839.2,"prompt_tokens":1268,"completion_tokens":47,"total_tokens":1315}
```

The trace above is the uncached-date case: the model requested `2020-01-02`, the tool returned a 70-character "not available" message in 1.4 ms, and the model reported that honestly rather than substituting another day's numbers.

The purpose is to separate two failures that look identical in terminal output: *the model chose the wrong tool* versus *the tool itself misbehaved*. When an eval fails, the trace says which.

## Project structure

```
stock_agent/
├── agent.py            ReAct agent assembly, tool-calling check
├── llm.py              vLLM client (separate module to avoid an import cycle)
├── prompts.py          system prompt (data limits, number discipline)
├── tools.py            6 LLM-facing tools
├── scan.py             scan entry point: limits, concurrency, graph reuse
├── briefing.py         briefing entry point: cache / skip / regenerate decision
├── watchlist.py        personal watchlist, validated against market data
├── report.py           report file I/O + deterministic composition
├── workflow/
│   ├── state.py        ScanState (reducer + Send payload) and BriefingState
│   ├── nodes.py        scan nodes; deterministic and LLM nodes separated
│   ├── graph.py        scan graph — dynamic Send fan-out
│   └── briefing.py     briefing graph — static parallel join + scan subgraph
├── query.py            structured results + pre-rendered text
├── render.py           number formatting, shared by tools / CLI / reports
├── screen.py           filtering, sorting, breadth, single-day shape metrics
├── trace.py            JSONL callback handler
├── cli.py              agent mode + briefing/watchlist + JSON data mode
└── sources/
    ├── twse.py         fetch and normalise
    ├── schema.py       normalised field definitions
    └── cache.py        trading-day file cache

tests/                  172 tests, fixtures recorded from live API
evals/                  behavioural regression cases + runner
scripts/                launchd job + install helper
.pi/                    optional Pi extension + agent instructions
```

~3,700 lines of Python across implementation and tests; ~300 lines of TypeScript; ~120 lines of shell.

Dependencies are deliberately few: `langgraph`, `langchain-openai`, `langchain-core`, `httpx`, `python-dotenv`, `pyyaml`. Notably **no pandas** — 1,377 rows sort fine with `sorted()`, and the dependency only earns its place once rolling windows across multiple trading days are needed. ([Why](docs/DECISIONS.md#5-no-pandas))

## Not implemented

Scoped out deliberately, with the reason in each case:

- **Intraday quotes** — available via an undocumented MIS endpoint; unstable and out of scope for end-of-day analysis.
- **Over-the-counter (TPEx)** — different field names, needs a normalisation layer extension.
- **Institutional net buy/sell** (`fund/T86`) — the endpoint currently returns HTTP 302; path has changed.
- **Fundamentals** — `BWIBBU_ALL` (P/E, P/B, yield) is verified working but is a different analytical axis.
- **Technical indicators** (MA/RSI/MACD) and volume-trend patterns (量增/量縮) — these need multiple trading days of history. The scheduled briefing job now accumulates one day file per trading day, so this becomes possible after a couple of weeks of runs; until then both `scan_market` and the briefing judge single-day shape only.
- **Scanning all 1,086 stocks** — `scan_market` is capped at 30 candidates. Going wider needs either a cheaper first-pass filter or a much faster model; at ~2.9 s/stock, 1,086 would take over an hour.
- **Static type checking** — runtime state is documented with `TypedDict`, but mypy or pyright is not yet enforced. Adding it will require tightening the dynamic dict payloads at the JSON and LangGraph boundaries.

## Contributing and licence

Development setup, quality checks, and design constraints are documented in [CONTRIBUTING.md](CONTRIBUTING.md). Security issues should follow [SECURITY.md](SECURITY.md) rather than a public issue.

Released under the [MIT License](LICENSE).

## Disclaimer

This project produces summaries of public market data. **It is not investment advice**, and the agent is instructed not to give any.
