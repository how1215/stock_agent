# Design decisions

Decisions where a reasonable alternative existed, what was chosen, and what it costs. Several of these reverse an earlier position in the project — those reversals are kept rather than hidden, because the reason for the reversal is the useful part.

---

### 1. The application owns the agent loop, not a coding harness

**Context.** The project started as an agent built on [Pi](https://github.com/earendil-works/pi), a terminal agent harness, with Python supplying only data. Pi would host the model, the conversation, and the tool loop.

**Problem with that.** Coding harnesses (Pi, Claude Code, and similar) are interactive developer tools, not application runtimes. Building on one as a foundation means:

- no service interface — no HTTP surface, no multi-user story, no authn or rate limiting;
- a system prompt and built-in toolset designed for editing code, including a `bash` tool that is arbitrary code execution (Pi's own documentation states it has no built-in permission system and recommends containers);
- session logs designed for humans to re-read, which cannot support offline evaluation;
- the agent loop controlled by the harness — `interrupt`, checkpointers, and custom routing are reachable only by working around event hooks;
- a globally-installed npm CLI as the deployment unit.

**Decision.** Python owns the agent loop, with its own tools, state, checkpointer, tracing, and eval harness. Pi is demoted to an optional thin shell over the JSON CLI (§10).

**Consequence.** More code than the harness-first approach — `agent.py`, `trace.py`, and `evals/` exist only because of this decision. In exchange, the eval harness becomes possible, which is the single most valuable thing in the repo for iterating on prompt and tool design.

---

### 2. A second LLM below the harness would have been redundant

**Context.** An intermediate design had the Pi extension pass a natural-language question to a Python-side LLM that parsed it into filter criteria and wrote a summary.

**Problem with that.** The harness model already sits at both ends. Parsing natural language into structured criteria *is* filling in tool parameters — accepting `question: string` throws away an already-parsed intent so a second model can re-parse it. Summarising a tool result is what the harness model does anyway. Two models, two interpretations, two chances to disagree.

**Decision.** In the Pi path, tools take structured parameters and Python does no LLM work at all. The parameter whitelist is expressed by the typebox schema rather than a prompt-embedded JSON schema plus retry loop.

**Consequence.** The Python-side LLM came back in a different form under §1 — but as the *primary* agent with the harness removed, not as a second layer underneath it. The redundancy was the problem, not the LLM.

---

### 3. Whole-market fetch as the only fetch path

**Context.** Fetching 1,377 rows to answer "what did TSMC close at?" looks wasteful. A per-stock endpoint exists: `www.twse.com.tw/exchangeReport/STOCK_DAY?stockNo=2330`.

**Measurements.** The whole-market endpoint is **one** HTTP request: 318 KB, 0.15 s. It is not 1,377 requests. The per-stock endpoint is one request *per stock*, returns a whole month rather than a single day, and uses an entirely different format — Chinese field arrays, comma-separated numbers (`"35,209,944"`), slash-separated ROC dates (`115/08/03`).

**Decision.** Whole-market only. `quote 2330` fetches the market and filters in memory.

**Consequence.** One parser, one field mapping, one cache shape. Screening ranked results across the whole market — which the per-stock path cannot do at all — comes for free. Cost: a single-stock lookup transfers more bytes than strictly necessary, which at 0.15 s and one request is not a cost worth a second parser.

---

### 4. Trading-day file cache, not SQLite

**Context.** Repeated queries shouldn't re-fetch. Options: SQLite with `(date, code)` upserts, an in-memory TTL cache, or one JSON file per trading day.

**Decision.** One JSON file per trading day, ~320 KB, with a `fetched_at` timestamp.

**Rationale.** No schema to maintain, no upsert logic, and day files are drop-in test fixtures. Reading the last N day files covers future rolling-window calculations without a data-layer change. SQLite's advantages — indexed cross-date queries — only start to pay once such queries exist, and none do yet.

**Consequence.** Cross-date aggregation will scan files rather than query indexes. At one file per trading day this is fine for years of data; if it stops being fine, that is the signal to migrate, and the day files convert trivially.

---

### 5. No pandas

**Context.** Filtering and sorting market data is pandas' home territory.

**Decision.** stdlib `sorted()` and list comprehensions.

**Rationale.** 1,377 rows. `screen.py` is 77 lines and its tests run in milliseconds. pandas would add a large dependency to save nothing measurable.

**When this flips.** Rolling windows across multiple trading days — moving averages, volume ratios versus a 5-day mean. That is genuinely awkward in stdlib and genuinely good in pandas. Adding it then is a one-module change, because `screen.py` is pure functions over lists of dicts.

---

### 6. A requested date reads only from cache

**Context.** The tools accept a `date` parameter. The natural implementation fetches that date from the API.

**Discovery.** The endpoints accept **no date parameter**. The Swagger spec declares none, and passing `?date=20260731` is silently ignored — the response is still the latest trading day. Verified across three parameter spellings.

**Decision.** A requested date reads the local cache and nothing else. If it isn't cached, return an error naming the dates that *are* available.

**Rationale.** The alternative is worse than useless. Calling the API for a requested historical date returns a different day's numbers, which would be presented under the requested day's label — a confidently wrong answer. An explicit "I don't have that date" is strictly better. A test asserts the API is not called on this path.

**Consequence.** Historical coverage accumulates only from daily fetching. This also deleted a planned "back off one trading day and retry" loop, which was built on the false premise that the API takes a date: it always returns the last trading day, so on holidays and mid-session there is nothing to back off from.

---

### 7. One shared renderer for two consumers

**Context.** Two consumers need the same data as text: the Python agent's tools, and external callers (the Pi extension) reading the JSON CLI.

**Options considered.** (a) The extension formats numbers in TypeScript. (b) A second CLI flag for text output. (c) The JSON payload carries a `text` field.

**Decision.** (c). `render.py` holds the formatting; `query.py` results include a `text` field produced by it; `tools.py` renders through it too.

**Rationale.** (a) reimplements the presentation rules in a second language, which guarantees drift — different rounding, different lot conversion, different wording, discovered eventually by a confused user. (b) needs two subprocess calls to get both representations. (c) gives both in one call with one implementation.

**Consequence.** A test asserts `query.market()["text"] == tools.get_market_index.invoke({})` for all three tools. The Pi extension contains no formatting code at all.

---

### 8. Tool errors return actionable text, not exceptions

**Context.** A model asks for ticker `9999`, which doesn't exist. The tool can raise, or return an error string.

**Decision.** Return text that tells the model how to recover:

> `查不到：9999。上市普通股代號為 4 位數字、首碼 1–9（如 2330），ETF 以 0 開頭（如 0050）。也可以直接用公司全名查詢，例如「台積電」。若該檔為上櫃股票，本工具查不到（僅涵蓋上市）。`

**Rationale.** A raised exception ends the turn. A message describing the code format and offering the name-based alternative lets the model retry correctly on its own. Partial success is also returned rather than discarded — asking for `["2330", "9999"]` returns TSMC's data *and* the miss.

**Consequence.** Tool functions never raise, so a genuine bug in the data layer surfaces as text rather than a stack trace. Mitigated by testing the layers underneath directly, where exceptions do propagate.

---

### 9. Two-tier verification

**Context.** How do you know an LLM agent still works after changing a prompt?

**Decision.** Two suites with different jobs.

`pytest` — 85 tests, ~0.4 s, no network, no model. Deterministic logic against recorded API responses. This is where correctness bugs get caught; the ETF misclassification in §11 was found here.

`evals/` — 8 cases, requires vLLM. Asserts on behaviour: which tools were called, with which arguments, which strings must appear, and which must **not**. Expected numbers are placeholders (`{taiex_close}`, `{close:2330}`) resolved against the data layer at run time, so cases survive daily data changes instead of rotting overnight.

**Rationale.** These fail for different reasons and must be runnable separately. A contributor without a GPU can still run `pytest`. Conversely, a passing `pytest` says nothing about whether the model picks the right tool — the thing most likely to break when a description is reworded.

---

### 10. Pi as an optional thin shell

**Decision.** `.pi/extensions/tw-stock.ts` registers three typed tools that shell out to the JSON CLI. It holds no application logic. Deleting it changes nothing.

**Implementation notes.** `node:child_process.execFile` rather than `pi.exec`, because `execFile`'s non-zero-exit behaviour (throws, with `stdout`/`stderr` attached) is well-defined and the CLI's error contract puts `{"error": …}` on stdout — the handler needs to unwrap that specific shape. `import.meta.url` locates the project root so the tools work regardless of the caller's working directory. Pi has no MCP support by design, so a CLI-backed extension is the supported integration path rather than a workaround.

**Status.** The CLI contract is covered by tests and the TypeScript passes a syntax check; a live Pi session has not been run, as `pi` was not installed in the development environment.

---

### 11. Two corrections worth recording

**The tool-calling precondition is a silent failure.** vLLM ignores tool definitions unless launched with `--enable-auto-tool-choice --tool-call-parser <parser>`. `bind_tools` raises nothing; the model simply never emits a tool call and instead answers from memory — plausibly, and wrongly. `cli verify` turns this into an explicit check, and it is the first step in the README because nothing else works without it.

**"4-digit code" does not mean "common stock."** The original rule for identifying common stocks was "4 digits, all numeric". A parametrised test on `0050` failed: it is 4 digits, all numeric, and an ETF. Checking live data found 8 such codes (`0050`, `0051`, `0052`, `0053`, `0055`, `0056`, `0057`, `0061`) — all ETFs. The correct rule is 4 digits with a leading 1–9, giving 1,086 common stocks rather than the 1,094 originally reported. Worth recording because the wrong rule produced *plausible* output: an ETF appearing in a "top gainers" list looks like a stock unless you know the code ranges.
