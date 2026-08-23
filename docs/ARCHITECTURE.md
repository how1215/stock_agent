# Architecture

How a question becomes an answer, what each layer is responsible for, and where the seams are.

## Layers

| Layer | Module | Responsibility | Knows about the LLM? |
|---|---|---|---|
| Agent | `agent.py` | ReAct loop, checkpointing | yes |
| Model | `llm.py` | vLLM client | yes |
| Prompt | `prompts.py` | Data limits, tool-selection rules, number discipline | yes |
| Tools | `tools.py` | 6 tool definitions, LLM-facing text, recoverable errors | yes |
| Scan | `scan.py`, `workflow/graph.py` | Per-stock fan-out graph | yes (injected into nodes) |
| Briefing | `briefing.py`, `workflow/briefing.py` | Daily briefing graph, cache/skip decision | yes (injected into nodes) |
| Query | `query.py` | Structured results + pre-rendered `text` | no |
| Render | `render.py` | Number formatting, wording | no |
| Screen | `screen.py` | Filtering, sorting, breadth, shape metrics, code classification | no |
| Watchlist | `watchlist.py` | Personal list, validated against market data | no |
| Report | `report.py` | Report file I/O, deterministic composition | no |
| Sources | `sources/` | Fetch, normalise, cache | no |

Everything from `query.py` down is ordinary, testable Python — which is why the test suite typically runs in under a second with no network and no model. The scan graph also tests without a model, because `build_scan_graph(llm)` takes it as a parameter rather than constructing it.

`llm.py` exists as its own module for a mundane reason worth stating: `agent.py` imports `tools.py` for the tool list, and `scan_market` inside `tools.py` needs a model. Putting `build_llm` in `agent.py` would make that a cycle.

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

## The scan graph: where a custom StateGraph earns its place

`create_react_agent` fits most of this problem: ask a question, call one or two tools, answer. For those three tools a hand-written graph would be ceremony.

One tool is different. `scan_market` answers open-ended descriptive questions — "find stocks that closed near their high with strong buying" — which cannot be expressed as numeric filters. Answering it means judging each candidate individually, and that is a map-reduce shape:

```
START → select_candidates ──(no candidates)──────────► no_candidates → END
                          └─(Send × N)──► read_one ──► aggregate → summarise → END
```

Three properties make this impossible in the ReAct loop:

| Property | Why ReAct can't | LangGraph mechanism |
|---|---|---|
| Branch count decided at run time | Tool calls are enumerated by the model, one at a time | `Send` from a conditional edge |
| N independent judgements in parallel | The loop is sequential by construction | Parallel node execution, `max_concurrency` |
| Merge N results back into one state | No shared accumulator across tool calls | `Annotated[list, operator.add]` reducer |

Deterministic and LLM nodes are deliberately separated. `select_candidates` (narrow 1,086 → N), `aggregate` (group by verdict), and `no_candidates` never touch a model — narrowing and counting are not judgement tasks. Only `read_one` and `summarise` call the model, and they receive it as a parameter so tests can inject a fake.

Two design details that matter more than they look:

**Branch payloads are minimal.** `Send` carries only `{focus, trade_date, quote}` — one stock, not the whole state. Branches cannot see each other, so one stock's reading cannot be influenced by another's. A single large prompt containing N stocks has no such guarantee.

**Branch failure is contained.** `read_one` never raises. A model error, or output that doesn't match the expected format, produces a reading with an `error` field; `aggregate` separates those out and the remaining N−1 complete normally. With one big prompt, one bad generation loses the entire batch.

The graph is mounted back as a single tool on the ReAct agent rather than replacing it — the agent still decides *whether* a question needs a scan at all, and an eval case guards against it reaching for the slow path on questions a numeric filter would answer.

Measured behaviour and the honest limits of the parallelism are in [DECISIONS §11](DECISIONS.md#11-a-hand-written-stategraph-for-per-stock-fan-out).

### Still out of reach

Scanning all 1,086 common stocks. At ~2.9 s/stock the cap is 30 for a reason. Going wider needs a cheaper first pass or a much faster model, not a different graph shape.

Notably, this whole tool is also work a coding harness structurally cannot do: Pi has no sub-agents, and the fan-out has to happen somewhere that owns the model client. It is the one place where an LLM layer below a harness would be justified rather than redundant.

## The briefing graph: static parallelism and subgraph composition

The daily pre-market briefing is a second hand-written graph, and it earns its place for different reasons than the scan graph.

```
START ─┬─► market_snapshot   index + breadth              (no LLM)
       ├─► watchlist_read    scan graph as a subgraph     (N LLM calls)
       └─► market_movers     gainers / losers / turnover  (no LLM)
                  │
                  ▼  join — compose_briefing waits for all three
            compose_briefing   one LLM call over already-computed numbers
                  │
                  ▼
            persist_report     deterministic markdown, written to disk
                  │
                 END
```

|  | scan graph | briefing graph |
|---|---|---|
| Parallelism | dynamic, homogeneous (N stocks, N known at run time) | static, heterogeneous (3 different jobs) |
| Mechanism | `Send` from a conditional edge | three edges out of `START`, join at the shared successor |
| Composition | none | mounts the scan graph as a subgraph |
| State merge | `Annotated[list, operator.add]` reducer | none needed — branches write disjoint keys |

The three branches are independent and all three must finish before anything can be synthesised. That is exactly LangGraph's join semantics; writing it as functions would mean managing a thread pool and the wait by hand.

### Subgraph composition, and the config that doesn't propagate

`watchlist_read` invokes the scan graph rather than reimplementing fan-out. `select_candidates` gained a second path for this: when `criteria["codes"]` is present it resolves those tickers via `screen.find_quotes()` instead of screening the whole market. Everything downstream — `Send`, `read_one`, `aggregate`, the failure isolation — is shared.

One thing that caught us: **a subgraph does not inherit the parent's `config`.** The first version called `build_scan_graph(llm).invoke(state)` with no config, so the watchlist readings silently ran without the concurrency setting. Measured cost: about a minute extra on a four-stock watchlist. The fix is one explicit `config={"max_concurrency": SCAN_MAX_CONCURRENCY}`.

### Where the time goes

Per-node measurement of a real run with a four-stock watchlist:

| Node | Time |
|---|---|
| `market_snapshot`, `market_movers` | 0.0 s (deterministic, parallel) |
| `watchlist_read` (4 stocks, parallel) | 48.9 s |
| `compose_briefing` (one call) | 32.1 s |
| **Total** | **81.0 s** |

The graph adds nothing measurable; the cost is entirely the two LLM nodes on a 31B local model. This is why the briefing is scheduled rather than interactive, and why `get_briefing` reads from disk by default — see [DECISIONS §14](DECISIONS.md#14-the-briefing-reads-from-disk-by-default).

### Deterministic output, LLM commentary

Only `compose_briefing` calls a model, and only for the closing paragraph. Section headers, the two dates, index figures, breadth counts, and every quote line are produced by `report.compose()`. If the model call fails, the observation becomes a failure note and the rest of the report is still complete and correct — asserted by a test.
