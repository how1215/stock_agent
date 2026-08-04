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

*(Counts below are current: 154 tests, 12 eval cases.)*

**Context.** How do you know an LLM agent still works after changing a prompt?

**Decision.** Two suites with different jobs.

`pytest` — 85 tests, ~0.4 s, no network, no model. Deterministic logic against recorded API responses. This is where correctness bugs get caught; the ETF misclassification in §11 was found here.

`evals/` — 8 cases, requires vLLM. Asserts on behaviour: which tools were called, with which arguments, which strings must appear, and which must **not**. Expected numbers are placeholders (`{taiex_close}`, `{close:2330}`) resolved against the data layer at run time, so cases survive daily data changes instead of rotting overnight.

**Rationale.** These fail for different reasons and must be runnable separately. A contributor without a GPU can still run `pytest`. Conversely, a passing `pytest` says nothing about whether the model picks the right tool — the thing most likely to break when a description is reworded.

---

### 10. Pi as the day-to-day interface, still holding no logic

**Original decision.** `.pi/extensions/tw-stock.ts` registers typed tools that shell out to the JSON CLI, as an optional convenience.

**Revised.** Pi is now the intended daily driver for the assistant, with six tools. The Python ReAct agent (`cli ask`) is kept as a second interface and as the vehicle for the eval suite.

**Why this doesn't contradict §1.** §1 argues against a coding harness as a **product runtime** — no service interface, an arbitrary-code-execution `bash` tool, session logs that can't support evaluation, an agent loop you don't control. None of that applies to a personal terminal tool for one user on their own machine. What §1 actually protects is *where the logic lives*, and that hasn't moved: Pi still holds nothing but argument marshalling and error unwrapping, and deleting the extension leaves the system fully functional through the CLI.

**Implementation notes.** `node:child_process.execFile` rather than `pi.exec`, because `execFile`'s non-zero-exit behaviour (throws, with `stdout`/`stderr` attached) is well-defined and the CLI's error contract puts `{"error": …}` on stdout — the handler needs to unwrap that specific shape. `import.meta.url` locates the project root so the tools work regardless of the caller's working directory. Pi has no MCP support by design, so a CLI-backed extension is the supported integration path rather than a workaround.

**One deliberate asymmetry.** The extension exposes watchlist `add` and `remove` but not `clear`; the ReAct agent's watchlist tool is read-only. A human is watching a Pi session, so incremental edits are fine there — but wiping the list on a misread instruction is not worth the convenience, and the Python agent has no such human check at all.

**Status.** The CLI contract is covered by tests and the TypeScript passes a syntax check; a live Pi session has not been run, as `pi` is not installed in the development environment.

---

### 11. A hand-written StateGraph for per-stock fan-out

**Context.** Three of the four tools are lookups that the prebuilt ReAct agent handles fine. The fourth answers descriptive questions ("stocks that closed near their high with strong buying") that no numeric filter expresses, which means judging each candidate individually.

**Decision.** A hand-written `StateGraph` in `workflow/`: `select_candidates` → `Send × N` → `read_one` (one LLM call per stock, parallel) → `aggregate` → `summarise`, mounted back onto the ReAct agent as one tool.

**Why not just one big prompt with N stocks?** Three reasons, in order of how much they actually matter:

1. **Failure containment.** One malformed generation loses the whole batch. With fan-out, a failed branch is recorded and the other N−1 complete.
2. **No cross-contamination.** Branch payloads carry one stock each, so branches cannot see one another. A single prompt gives no such guarantee.
3. **Parallelism.** Real, but smaller than expected — see below.

**Measured.** Same 8 stocks, `gemma-4-31B-it-qat-w4a16-ct` on local vLLM:

| Stocks | Concurrency | Wall clock | Per stock |
|---|---|---|---|
| 8 | 1 | 84.9 s | 10.6 s |
| 8 | 8 | 45.9 s | 5.7 s |
| 16 | 8 | 54.5 s | 3.4 s |
| 16 | 16 | 46.6 s | 2.9 s |

**The honest reading of these numbers.** Concurrency 8 buys 1.85× over serial, not 8×. The local model is compute-bound, so vLLM's continuous batching improves throughput but nowhere near linearly. A direct probe made this look worse than it is — 8 trivial concurrent requests showed almost no gain — but at realistic prompt sizes the batching does help: 16 stocks at concurrency 16 (46.6 s) costs about the same as 8 stocks at concurrency 8 (45.9 s), i.e. twice the work for the same wall clock. That is what set the default to 16.

**Consequence.** `MAX_SCAN_LIMIT` is 30, far below `MAX_SCREEN_LIMIT` of 100, because here the limit is latency rather than context. Scanning all 1,086 common stocks would take over an hour and is explicitly out of scope. The tool description and system prompt both push numeric questions to `screen_stocks`, and an eval case with `forbid_tools` asserts the model doesn't reach for the slow path unnecessarily.

**What it does not do.** Volume-trend judgements (量增/量縮) need a multi-day average, and the upstream endpoint only serves the latest trading day, so the model is given single-day shape metrics only — amplitude, close position within the day's range, body size, gap. Those are computed in `screen.shape_metrics()`, not by the model.

---

### 12. Pre-market, not intraday

**Context.** Building a market-watching assistant naturally suggests live quotes. Three readings of "analyse the opening data" were on the table: pre-market homework from the previous close, a snapshot shortly after the open, or continuous intraday monitoring.

**Constraint.** The TWSE open API serves end-of-day data only, and the publication lag is longer than "after the close" suggests: measured at 20:50 on a Tuesday, over seven hours past the 13:30 close, it was still serving the previous Monday. Today's session appears by the following morning. `OpeningPrice` is likewise the previous day's open. Intraday means a second data source: `mis.twse.com.tw/stock/api/getStockInfo.jsp`, which works (verified) but is undocumented, needs a `Referer` header, uses abbreviated field names (`o`/`h`/`l`/`z`/`tv`), has no SLA, and would require a second parser and field mapping.

**Decision.** Pre-market only. The briefing runs at 08:30 on the previous close.

**Rationale.** Pre-market homework — where is the index, how did my watchlist close, what moved — is genuinely useful and needs no new data source. Intraday monitoring additionally needs poll scheduling, state diffing, alert de-duplication, and rate-limit backoff. That is a larger project resting on an endpoint that can break without notice.

**Consequence.** The assistant cannot answer "what is happening right now". The report and prompts state the trading date explicitly so this is never ambiguous. Volume-trend judgements (量增/量縮) also remain out of reach for a different reason: they need a multi-day average, and history only accumulates from daily runs.

---

### 13. Scheduling on the OS, offered but not default

**Context.** The briefing was originally meant to run every trading day before the open.

**Decision.** macOS `launchd`, weekdays 08:30, via `scripts/install-launchd.sh` and a wrapper script. No scheduler inside the application.

**Rationale.** Neither Pi nor the Python agent has a scheduler, and adding one would mean a long-lived process to keep alive. launchd already solves "run this at a time, log the output, survive reboots". The wrapper exists because launchd provides almost no environment — `uv` has to be located explicitly rather than assumed on `PATH`.

**What actually happened.** The job registered fine and then failed on every run:

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../scripts/briefing.sh: Operation not permitted
```

This is macOS TCC. `~/Documents`, `~/Desktop`, and `~/Downloads` are protected; an interactive Terminal has been granted access, but the process launchd execs has not. The script was `-rwxr-xr-x`, ran fine from a shell, and had no quarantine attribute — the only difference was who was executing it.

The available fixes are both unattractive. Granting Full Disk Access to `/bin/bash` gives every shell script on the machine full disk access. Moving the project out of `~/Documents` works cleanly but is a change to someone's directory layout that a repo has no business demanding.

**Revised decision.** Scheduling is optional, and on-demand generation is the documented default. `install` now detects a TCC-protected path and refuses with an explanation instead of registering a job whose failures are only visible in a log file. A silently-broken scheduled job is worse than no scheduled job.

**Consequence, and it is a real one.** History now accumulates only on days the assistant is actually used. Any query writes that trading day's cache file as a side effect, so regular use largely covers it — but a missed day cannot be backfilled, because the endpoint has no date parameter (§6). That is precisely what stands between this project and moving averages or volume-trend judgements.

**Two behaviours that mattered more than the scheduling itself:**

*Weekend and holiday runs must not overwrite.* The job always runs with `--refresh`, but `get_briefing` compares the newest available trading day against what the existing report was already based on and returns `skipped` when they match. Measured: 0.6 s to skip versus 81 s to generate.

*Incoherent dates are rejected.* An early version accepted `--report-date 2020-01-02` and cheerfully wrote "盤前提要 2020-01-02（依據 2026-08-03 收盤）" — a file whose two dates contradict each other but which looks entirely normal. A pre-market report must be based on data from *before* its own date, so a trading date later than the report date now raises.

**Portability note.** The plist is macOS-specific; a Linux deployment would need a cron entry or systemd timer. The wrapper script is portable, and Linux has no TCC equivalent, so the protected-directory problem doesn't arise there.

---

### 14. The briefing reads from disk by default

**Context.** Generating a briefing takes ~81 s (measured: 48.9 s for a four-stock watchlist fan-out, 32.1 s for the closing paragraph).

**Decision.** `get_briefing` returns the saved report unless `refresh=True`. The scheduled job is what normally writes it.

**Rationale.** An assistant that takes 81 s to answer "what should I watch today" is one you stop using. Reading the file is instantaneous. The generation cost is real but it belongs at 08:30 when nobody is waiting.

**Consequence.** The tool description and Pi guidelines have to be explicit that `refresh` is expensive, or the model will use it for politeness. The `text` field in the JSON payload carries the whole report, so a Pi tool call is one subprocess and no formatting work.

---

### 15. A judgement axis that actually discriminates

**Context.** The briefing reads each watchlist holding through the scan graph, which classifies as `符合` / `部分符合` / `不符合` against a stated focus.

**What went wrong.** The first focus was "is this worth paying attention to at today's open". Every single holding came back `符合` — correctly, since a collapsing stock is also worth attention. The verdict column carried zero information.

**Decision.** Narrow the focus to something falsifiable: did the stock close with buying pressure (closed up, closed near the day's high, no significant fade from the open). Now `不符合` is itself useful — in the sample run 台積電 (−2.27 %, close position 17 of 100) is correctly separated from 聯發科 (+9.99 %, close position 100).

**Also.** The report prints the axis above the readings. Without it, a reader has no way to know what `符合` means, and the classification is worse than useless because it looks meaningful.

**Generalisation worth keeping.** A classification prompt that cannot produce the negative class is not classifying. This is cheap to spot by eyeballing one real run, and invisible in unit tests with a stubbed model.

---

### 16. The daily routine lives in a skill, and what that costs

**Context.** The morning routine is: make sure today's data is fetched, read the briefing, condense it into what to watch. Three steps, run once per trading day.

**Decision.** A Pi skill (`.pi/skills/premarket/SKILL.md`) that sequences existing tools. No new capability, no new code path.

**What makes it more than ceremony.** A skill that just wraps "call one tool and repeat the answer" would be pure overhead — the tool description already covers that. This one encodes an ordering the model would otherwise optimise away: reading a cached briefing performs **zero HTTP requests** (measured) and so never writes that day's cache file. Because the endpoint has no date parameter (§6), a trading day not fetched is lost permanently. Step 1 exists solely to keep history accumulating, and a model reasoning about efficiency would skip it. Prose is the right place for a constraint like that; a tool description can't express "call this other tool first, for a reason unrelated to your immediate goal".

**What it costs.** A skill is prose interpreted by a model, so the eval harness — which drives the Python agent — cannot exercise it. This is §1's argument arriving from the other direction: choosing a harness as the interface means giving up mechanical verification of what happens there.

**What was salvaged.** Reference integrity is checkable without a model, and it catches a genuinely silent failure mode: a skill instructing the model to call a tool that doesn't exist produces no error, the model simply substitutes another. `tests/test_pi_assets.py` cross-checks tool names in `SKILL.md` and `AGENTS.md` against the extension's registrations, CLI subcommands the extension invokes against `cli.py`'s parsers, and the presence of the step-1 constraint and the trading-date discipline in every model-facing document. The tests were mutation-checked by injecting a bad tool name.

**What remains manual.** Whether the model actually follows the ordering, and whether its condensed output beats reading the raw report. No amount of static checking reaches that.

---

### 17. Three corrections worth recording

**Verdict labels that are substrings of each other.** `read_one` asks the model for `符合` / `部分符合` / `不符合`. When the model ignores the delimited format, the fallback searches the raw text for a label — and the original implementation iterated the tuple in declaration order. `符合` is a substring of *both* other labels, so `"我認為不符合這個條件"` was classified as **符合**: the exact opposite conclusion, reported confidently. Found on the first end-to-end run with a fake model, before any real model saw it. The fix is to match longest-first, and the regression test asserts all three labels resolve correctly from unformatted text.

**The tool-calling precondition is a silent failure.** vLLM ignores tool definitions unless launched with `--enable-auto-tool-choice --tool-call-parser <parser>`. `bind_tools` raises nothing; the model simply never emits a tool call and instead answers from memory — plausibly, and wrongly. `cli verify` turns this into an explicit check, and it is the first step in the README because nothing else works without it.

**"4-digit code" does not mean "common stock."** The original rule for identifying common stocks was "4 digits, all numeric". A parametrised test on `0050` failed: it is 4 digits, all numeric, and an ETF. Checking live data found 8 such codes (`0050`, `0051`, `0052`, `0053`, `0055`, `0056`, `0057`, `0061`) — all ETFs. The correct rule is 4 digits with a leading 1–9, giving 1,086 common stocks rather than the 1,094 originally reported. Worth recording because the wrong rule produced *plausible* output: an ETF appearing in a "top gainers" list looks like a stock unless you know the code ranges.
