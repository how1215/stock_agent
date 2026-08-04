# Taiwan Stock Market Agent

A natural-language query agent for Taiwan Stock Exchange (TWSE) market data, built as a **LangGraph ReAct agent** running against a **locally-hosted LLM via vLLM**. Ask questions in plain language; the agent decides which tools to call, and every number in the answer traces back to a deterministic Python computation.

The application owns the agent loop — its own tools, state, checkpointing, tracing, and evaluation harness — rather than delegating it to an off-the-shelf coding harness. [The reasoning behind that choice is documented.](docs/DECISIONS.md#1-the-application-owns-the-agent-loop-not-a-coding-harness)

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
- [Optional: Pi extension](#optional-pi-extension)
- [Data source and its limitations](#data-source-and-its-limitations)
- [Testing and evaluation](#testing-and-evaluation)
- [Observability](#observability)
- [Project structure](#project-structure)
- [Not implemented](#not-implemented)

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
        │  tools.py    4 tools, LLM-facing     │
        └────────┬─────────────────────┬───────┘
                 │                     │ scan_market
                 │                     ▼
                 │     ┌──────────────────────────────────┐
                 │     │  workflow/  custom StateGraph    │
                 │     │  Send fan-out → per-stock LLM    │
                 │     │  → reducer fan-in → summarise    │
                 │     └──────────────────┬───────────────┘
                 ▼                        │
        ┌──────────────────────────────────────┐
        │  query.py    structured results      │──► JSON CLI ──► Pi extension
        └──────────┬───────────────┬───────────┘                 (optional)
                   ▼               ▼
            screen.py         render.py
         (filter / sort)   (number formatting)
                   │
                   ▼
        ┌──────────────────────────────────────┐
        │  sources/  cache ──► TWSE open API   │
        └──────────────────────────────────────┘
```

Three of the four tools are simple lookups. The fourth, `scan_market`, mounts a **hand-written `StateGraph`** that fans out one LLM call per candidate stock and reduces the results — a shape the prebuilt ReAct loop structurally cannot express. [Why it earns its place, with measurements.](docs/DECISIONS.md#11-a-hand-written-stategraph-for-per-stock-fan-out)

Two properties matter more than the box diagram:

**The LLM never does arithmetic.** Percentage change, lot conversion, and 億元 scaling are all computed in Python and rendered to text before the model sees them. The system prompt forbids the model from producing any number that did not come from a tool result, and an eval case asserts it doesn't.

**`render.py` is shared, deliberately.** Both the Python agent's tools and the JSON CLI's `text` field render through it, so the text a local model sees and the text an external caller receives are byte-identical. A test asserts this equality — without it, two presentation implementations would drift.

Full data flow and layer responsibilities: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Design highlights

| | |
|---|---|
| **Tool interface as the contract** | Tool descriptions are the model's only basis for choosing a tool, so they carry the usage rules. Errors return *actionable* text (`"代號 9999 不存在。上市普通股代號為 4 位數字、首碼 1–9…"`) rather than exceptions, letting the model self-correct instead of dead-ending. |
| **Two-tier verification** | `pytest` covers deterministic logic against recorded API fixtures — no network, no LLM, 0.4s. A separate eval harness covers agent *behaviour*: did it pick the right tool, with the right arguments, without inventing numbers? |
| **Evals that don't rot** | Expected values are placeholders (`{taiex_close}`, `{close:2330}`) resolved against the data layer at run time, so cases stay valid as market data changes daily. |
| **Honest failure over plausible answers** | The upstream endpoint serves only the latest trading day. A request for a date not in the local cache returns an explicit error instead of silently substituting today's numbers — a wrong-date answer is worse than a refusal. |
| **Fail-loud dependency check** | vLLM silently ignores tool definitions unless launched with the right flags; `bind_tools` does *not* raise. `cli verify` turns that into an explicit pass/fail. |
| **Fan-out with failure isolation** | The scan graph runs one LLM call per stock in parallel. A single branch that errors or returns unparseable output is recorded as a failure and the rest of the batch completes — no truncation, no cross-contamination between stocks. Measured 1.85× faster than serial on a compute-bound local model. |

Trade-offs and rejected alternatives: [docs/DECISIONS.md](docs/DECISIONS.md).

## Quickstart

**Prerequisites:** Python 3.13+, [uv](https://docs.astral.sh/uv/), and a vLLM server.

vLLM **must** be started with tool calling enabled, or the ReAct loop cannot work:

```bash
--enable-auto-tool-choice --tool-call-parser <parser>
```

The parser depends on the model (`hermes` for Qwen-family, `llama3_json` for Llama 3, `mistral` for Mistral). Without these flags `bind_tools` raises no error — the model simply never emits a tool call, which is the hardest failure mode to diagnose. Verify explicitly:

```bash
uv sync
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

### Data-only mode (no LLM)

The same CLI exposes the data layer as JSON. Useful for scripting, and it is what the Pi extension calls.

```bash
uv run python -m stock_agent.cli market
uv run python -m stock_agent.cli quote 2330 鴻海
uv run python -m stock_agent.cli screen --sort-by volume --limit 10
```

Contract: **stdout is JSON only**, logs go to stderr, and failures exit non-zero with `{"error": "..."}` on stdout. Every payload carries `trade_date` and a pre-rendered `text` field. The contract is covered by tests, not just documented.

## Optional: Pi extension

[Pi](https://github.com/earendil-works/pi) is a terminal agent harness. `.pi/extensions/tw-stock.ts` registers three typed tools (`stock_market`, `stock_quote`, `stock_screen`) that shell out to the JSON CLI above, so the market data is queryable from a Pi session.

```bash
pi -e .pi/extensions/tw-stock.ts
```

This is a **thin shell, not a dependency** — Pi owns no application logic and removing the extension changes nothing about the system. Pi has no MCP support by design, so a CLI-backed extension is the supported integration path.

The extension deliberately does no number formatting of its own: it forwards the `text` field from the JSON payload. Reimplementing the formatting rules in TypeScript would guarantee eventual drift.

> Status: the CLI contract the extension depends on is covered by tests, and the TypeScript passes a syntax check. End-to-end verification inside a live Pi session has not been run — `pi` was not installed in the development environment.

## Data source and its limitations

| Endpoint | Content |
|---|---|
| `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | Daily OHLCV for all listed stocks, whole market in one response |
| `.../exchangeReport/MI_INDEX` | Daily closing values for all TWSE indices |

No API key required. Measured: one request, 318 KB, 0.15 s, 1,377 rows (1,086 of them common stocks).

Three limitations shape the entire design:

1. **Listed (上市) only** — over-the-counter (上櫃/TPEx) is a separate API and is out of scope. The agent says so when asked.
2. **End-of-day data, not real-time quotes.** Calling during market hours returns the **previous** trading day. The agent therefore always states the actual trading date rather than saying "today".
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
uv run pytest                      # 109 tests, ~0.4s, no network, no LLM
uv run python evals/run_evals.py   # 10 behavioural cases, requires vLLM
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

Two of the cases are a matched pair guarding tool *selection* cost — `scan_market` is an order of magnitude slower than `screen_stocks`, so one case asserts it is used for a descriptive condition and another asserts it is **not** used for a purely numeric one (`forbid_tools`).

This is regression testing for prompt and tool-description changes: swap the model or reword a description, run the suite, and see immediately whether behaviour degraded. Current state: 10/10 passing on `gemma-4-31B-it-qat-w4a16-ct`.

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
├── tools.py            4 LLM-facing tools
├── scan.py             scan entry point: limits, concurrency, graph reuse
├── workflow/           hand-written StateGraph for per-stock fan-out
│   ├── state.py        state with an accumulating reducer + Send payload type
│   ├── nodes.py        deterministic nodes and LLM nodes, separated
│   └── graph.py        assembly; llm injected for testability
├── query.py            structured results + pre-rendered text
├── render.py           number formatting, shared by tools and CLI
├── screen.py           filtering, sorting, single-day shape metrics
├── trace.py            JSONL callback handler
├── cli.py              agent mode + scan mode + JSON data mode
└── sources/
    ├── twse.py         fetch and normalise
    ├── schema.py       normalised field definitions
    └── cache.py        trading-day file cache

tests/                  109 tests, fixtures recorded from live API
evals/                  behavioural regression cases + runner
.pi/                    optional Pi extension + agent instructions
```

~2,400 lines of Python across implementation and tests; ~175 lines of TypeScript.

Dependencies are deliberately few: `langgraph`, `langchain-openai`, `langchain-core`, `httpx`, `python-dotenv`, `pyyaml`. Notably **no pandas** — 1,377 rows sort fine with `sorted()`, and the dependency only earns its place once rolling windows across multiple trading days are needed. ([Why](docs/DECISIONS.md#5-no-pandas))

## Not implemented

Scoped out deliberately, with the reason in each case:

- **Intraday quotes** — available via an undocumented MIS endpoint; unstable and out of scope for end-of-day analysis.
- **Over-the-counter (TPEx)** — different field names, needs a normalisation layer extension.
- **Institutional net buy/sell** (`fund/T86`) — the endpoint currently returns HTTP 302; path has changed.
- **Fundamentals** — `BWIBBU_ALL` (P/E, P/B, yield) is verified working but is a different analytical axis.
- **Technical indicators** (MA/RSI/MACD) and volume-trend patterns (量增/量縮) — these need multiple trading days of history, and the upstream endpoint only serves the latest day, so `data/` has to accumulate first. `scan_market` therefore judges single-day shape only.
- **Scanning all 1,086 stocks** — `scan_market` is capped at 30 candidates. Going wider needs either a cheaper first-pass filter or a much faster model; at ~2.9 s/stock, 1,086 would take over an hour.
- **Scheduled daily collection** — history currently accumulates only when a query happens to run. A cron/launchd job after market close would decouple accumulation from ad-hoc use, and is the prerequisite for the two items above.

## Disclaimer

This project produces summaries of public market data. **It is not investment advice**, and the agent is instructed not to give any.
