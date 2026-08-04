# Architecture

How a question becomes an answer, what each layer is responsible for, and where the seams are.

## Layers

| Layer | Module | Responsibility | Knows about the LLM? |
|---|---|---|---|
| Agent | `agent.py` | ReAct loop, model client, checkpointing | yes |
| Prompt | `prompts.py` | Data limits, tool-selection rules, number discipline | yes |
| Tools | `tools.py` | 3 tool definitions, LLM-facing text, recoverable errors | yes |
| Query | `query.py` | Structured results + pre-rendered `text` | no |
| Render | `render.py` | Number formatting, wording | no |
| Screen | `screen.py` | Filtering, sorting, code classification | no |
| Sources | `sources/` | Fetch, normalise, cache | no |

Only the top three layers know an LLM exists. Everything from `query.py` down is ordinary, testable Python — which is why the test suite runs in 0.4 s with no network and no model.

## Request lifecycle

```
"今天漲幅前 10 名？"
    │
    │  1. agent.invoke({"messages":[…]}, config={thread_id, callbacks})
    ▼
ReAct loop ─── system prompt + conversation history ──► vLLM
    │                                                     │
    │  2. model emits tool_call                            │
    │     screen_stocks(sort_by="change_pct",              │
    │                   order="desc", limit=10)            │
    ▼                                                     │
tools.screen_stocks                                        │
    │  3. delegates to query.screen                        │
    ▼                                                     │
query.screen                                               │
    │  4. cache.load_or_fetch(None)                        │
    │       cache hit if fetched < 600s ago,               │
    │       otherwise one HTTP request to TWSE             │
    │  5. screen.screen(...)  ← all arithmetic here        │
    │  6. render.render_screen(...) → compact text         │
    ▼                                                     │
tool result (text) ────────────────────────────────────────┘
    │
    │  7. model writes the answer from that text alone
    ▼
final message  +  traces/<thread_id>.jsonl
```

Step 5 is the load-bearing one: percentage change, lot conversion (`股` → `張`), and 億元 scaling all happen in Python. By the time the model sees anything, every number is final. The system prompt forbids producing numbers absent from the tool output, and an eval case asserts the model doesn't invent them when a lookup fails.

## Key seams

**`query.py` is the single data seam.** Both `tools.py` and the JSON CLI go through it, so tests stub exactly one function (`query.cache.load_or_fetch`) to make both paths run on fixtures.

**`render.py` is the single presentation seam.** `query.py` results carry a `text` field rendered by it, and `tools.py` renders through it too. A test asserts `query.market()["text"] == tools.get_market_index.invoke({})` — the equality is what stops two formatting implementations from drifting apart. It also means the Pi extension needs no formatting code: it forwards `text`.

**`sources/schema.py` is the single naming seam.** The two upstream endpoints disagree on field naming — `STOCK_DAY_ALL` uses English keys (`ClosingPrice`, `TradeVolume`), `MI_INDEX` uses Chinese (`收盤指數`, `漲跌點數`). Normalisation to one snake_case vocabulary happens once, at the boundary. Nothing above `sources/` ever sees a raw upstream field name.

## State and memory

`create_react_agent(checkpointer=InMemorySaver())` plus a `thread_id` gives multi-turn context:

```
> 台積電收多少？
2026-08-03 收盤，台積電（2330）收盤價為 2,370.00 元…

> 那鴻海呢？          ← resolved against prior turn, no restatement needed
2026-08-03 收盤，鴻海（2317）收盤價為 253.00 元…
```

`InMemorySaver` is per-process, which suits the CLI. Cross-process resumable sessions would swap in a SQLite checkpointer — a constructor change, since `build_agent()` takes the checkpointer as a parameter.

## Caching

`data/<trading-date>.json`, one file per trading day, ~320 KB each, each carrying a `fetched_at` timestamp.

The timestamp exists because of an upstream constraint: the endpoints accept no date parameter and always return the latest trading day. Freshness therefore *cannot* be determined by comparing dates — a cached file for `2026-08-03` might be the latest trading day, or might be stale because the market has since closed on `2026-08-04`. Only fetch time can answer that, so the cache uses a TTL (`FRESH_TTL_SECONDS`, default 600).

A request for a specific date reads the cache and never falls back to the API, because the API would return a *different* day's numbers under the requested day's label. See [DECISIONS §6](DECISIONS.md#6-a-requested-date-reads-only-from-cache).

A side benefit: day files are drop-in test fixtures. `tests/fixtures/stock_day_all.json` is a subset of a real response.

## Observability

`trace.py` is a `BaseCallbackHandler` passed via `config={"callbacks": […]}`. It writes JSONL and keeps the tool-call sequence in memory, which is what the eval runner inspects to assert *which* tools were called with *what* arguments — behaviour that is invisible in the final answer text.

The design goal is to distinguish two failures that produce identical-looking terminal output:

| Symptom | `tool_start` event | Diagnosis |
|---|---|---|
| Wrong answer | absent | Model didn't call the tool — prompt or tool-description problem |
| Wrong answer | present, args wrong | Model misunderstood the parameters — description problem |
| Wrong answer | present, args right | Data or logic problem — the Python side |

## Extension points

**Adding a data source.** Add a fetcher + normaliser in `sources/`, mapping to the existing `QUOTE_FIELDS` vocabulary. Nothing above `sources/` changes if the vocabulary holds. TPEx (over-the-counter) is the obvious next one and is the reason normalisation is a separate concern rather than inline parsing.

**Adding a tool.** Define it in `tools.py` and append to `ALL_TOOLS`. The tool description is the model's only basis for choosing it, so that is where the effort goes — not the prompt.

**Adding a Pi tool.** Register in `.pi/extensions/tw-stock.ts` against a JSON CLI subcommand. The extension holds no logic beyond argument marshalling and error unwrapping.

## When a custom StateGraph becomes necessary

`create_react_agent` fits this problem: ask a question, call one or two tools, answer. A hand-written `StateGraph` would be ceremony.

That changes when the work no longer fits one context window. Reading all 1,086 common stocks individually — per-stock pattern interpretation, then an aggregate summary — is a map-reduce shape that needs explicit fan-out and fan-in, per-branch retry, and partial-failure handling. That is what `StateGraph` with `add_conditional_edges` is actually for, and it would be mounted back as a single tool on the ReAct agent rather than replacing it.

Notably, this is also work a coding harness structurally cannot do: Pi has no sub-agents, and the fan-out has to happen somewhere that owns the model client. It is the one scenario where a second LLM layer below the harness would be justified rather than redundant.
