# secondpass: Personal Security Review Agent (Week 4)

> Standalone repo, not folded into the internship monorepo since this is meant to potentially grow beyond the assignment. Same rules as always, log prompts that actually changed something, note what I caught or decided myself, keep it in small steps.

## Why this project

Landed on a security review agent since I have three real security bugs I found and fixed myself in Weeks 2 and 3 (a cross-user data access gap, a tag-filter bug, a logout race condition). An agent that remembers those specifically, and checks new code against them, is something a plain ChatGPT session can't do without me re-pasting my whole history every time, that's the actual point of persistent memory here, not just satisfying the assignment's requirement for it.

Deliberately positioned this as a personal tool, not a general security scanner, looked into CodeRabbit and Semgrep and both already do a much bigger version of "AI reviews your code for security issues." The honest pitch is "a second pass that checks my own recurring mistakes," not "a replacement for real security tooling."

Decided against LangChain/LangGraph for this one on purpose, the assignment's topics list mentions them but the actual checklist doesn't require them, and building the tool-calling loop by hand was the point, so I'd actually understand what a hook and a planner loop are instead of importing them from a framework.

---

## 1. Project skeleton, LLM provider switch, Semgrep scanner, basic CLI

**Tool:** Cursor (Auto/Composer)
**Goal:** Get the core plumbing working before touching memory or the agent loop, provider-agnostic LLM client, a Semgrep wrapper, and a bare CLI that just runs the scanner.

**Prompt used:**

```
Scaffold a Python CLI project called "secondpass" for a personal security review agent.

1. Project structure:
   secondpass/
     app/
       llm.py       # provider-agnostic chat() function
       scanner.py   # Semgrep wrapper
       cli.py       # Typer CLI entrypoint
     requirements.txt
     .env.example
     .gitignore

2. llm.py: a chat(messages, tools=None) function that reads LLM_PROVIDER from env (groq, gemini, or openrouter, default groq), and routes to the right client:
   - Groq and OpenRouter: both OpenAI-compatible, use the openai SDK with a different base_url for each
   - Gemini: use Google's OpenAI-compatible endpoint if available, so we can keep one code path, otherwise the native Google SDK
   Read the API key and optional model override from env per provider. Keep this simple, no LangChain, no abstraction beyond this one function.

3. scanner.py: a run_static_scan(paths: list[str]) function that:
   - Runs `semgrep scan --config p/python --config p/javascript --json` on the given paths via subprocess
   - Parses the JSON output's results array
   - Normalizes each finding into {rule_id, severity, path, line, message, snippet}
   - Handles the case where semgrep isn't installed (clear error message) and where it finds zero issues (empty list, not an error)

4. cli.py: a Typer CLI with one command for now: `review <path>`, which just runs the scanner and prints normalized findings as a table. Don't wire up the LLM, memory, or web search yet, just confirm the scanner works end to end.

5. .env.example with LLM_PROVIDER, GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, LLM_MODEL (optional).

After implementing: run `review` against a real file with a deliberate issue (e.g. a hardcoded secret or eval() call) and confirm Semgrep's finding comes through the CLI in the normalized format.
```

**Result:** Verified via WSL, Semgrep correctly flagged a `subprocess-shell-true` issue in a deliberately vulnerable test file, showed up correctly normalized in the Rich table output. Went with Semgrep over Bandit + eslint-plugin-security after comparing both, one tool covering both Python and JS/TS with a single subprocess call and JSON parser was simpler than wiring up two separate scanners for a one-week project. Confirmed Semgrep's CLI is fully free (no account, no usage limits), so no cost concern there.

**Review notes:** This was the first genuinely unfamiliar Python territory for me this whole internship, subprocess calls and JSON parsing aren't shaped like the FastAPI/React code I already had intuition for. Went through scanner.py and llm.py line by line rather than skimming, specifically the subprocess.run() call, the .get(key, default) defensive pattern used throughout for reading Semgrep's JSON, and the \_PROVIDERS dict driving the whole multi-provider switch. Worth remembering: llm.py only works this cleanly because Groq, Gemini, and OpenRouter all chose to expose OpenAI-shaped APIs, if a provider didn't follow that convention this whole pattern breaks.

---

## 2. Persistent memory with ChromaDB

**Tool:** Cursor (Auto/Composer)
**Goal:** Seed real personal security lessons and get semantic search working before touching the rest of the agent.

**Prompt used:**

```
Add persistent memory to secondpass using ChromaDB.

1. Create a JSON file, security_lessons.json, with 3-5 real entries structured as:
   {
     "id": "lesson-1",
     "type": "Broken Access Control",
     "pattern": "GET route returns a resource without checking requester owns it",
     "bad_example": "short code snippet",
     "fix": "scope every read query by the authenticated user's ownership or admin role",
     "source": "Internship Week 3"
   }
   Help me write these based on my three real bugs: the cross-user read access gap, the tag-filter substring match bug, and the logout race condition.

2. Create app/memory.py with:
   - A function to initialize a persistent ChromaDB collection (stored locally on disk, not in-memory, so it survives between runs)
   - A seed_memory() function that loads security_lessons.json and embeds each lesson into the collection (only if the collection is empty, so re-running doesn't duplicate entries)
   - A search_memory(query: str, n_results: int = 3) function that returns the closest matching lessons
   - A save_finding(finding: dict) function that adds a new confirmed lesson to the collection at runtime

3. Add a CLI command, secondpass search-memory "some query", so I can test memory retrieval standalone before wiring it into the full agent loop.

After implementing: seed the memory, then run a test query that should semantically match one of the lessons (e.g. searching "user can read someone else's data" should surface the cross-user access lesson) even though the wording doesn't match exactly, confirm the embedding-based search actually works, not just exact keyword matching.
```

**Result:** Ended up with 5 seeded lessons, my three real bugs plus two related patterns (IDOR via client-supplied user_id, and overly verbose errors leaking data). Verified semantic matching actually works, not just keyword overlap: querying "user can read someone else's data" correctly surfaced the cross-user access lesson first even with zero exact word overlap with the stored pattern text, same for a tag-filter-related query matching the tag-filter lesson. Re-running the seed step correctly returned 0 new entries instead of duplicating.

---

## 3. Web search skill (Tavily)

**Tool:** Cursor (Auto/Composer)
**Goal:** Add the web-search skill, mirroring a pattern I'd already built in a past project.

**Prompt used:**

```
Add a web search skill to secondpass using Tavily, mirroring the pattern from my existing Multi-Agent Research Assistant project.

1. Create app/websearch.py with a search_web(query: str, max_results: int = 3) function that calls the Tavily API and returns normalized results: {title, url, snippet}.
2. Add TAVILY_API_KEY to .env.example.
3. Add a standalone CLI command, secondpass search-web "some query", so I can test it in isolation before wiring it into the full agent loop.

After implementing, run a test query relevant to this project, something like "OWASP broken access control A01" and confirm real, relevant results come back with title/url/snippet populated.
```

**Result:** Worked as expected. Worth noting, Cursor initially borrowed my Tavily key from an old project's .env to run the live test rather than assuming I had one set up yet in this project, flagged it honestly instead of just quietly reusing it. Added my own TAVILY_API_KEY to secondpass's own .env afterward so this project doesn't depend on another project's config existing on my machine.

---

## 4. The core agent loop, tool registry, and the logging hook

**Tool:** Cursor (Auto/Composer)
**Goal:** This is the actual heart of the assignment, the planner that decides which tools to call and when, plus the hook that logs every tool call.

**Prompt used:**

```
Build the core agent loop for secondpass, tying together the scanner, memory, and web search skills built so far.

1. Create app/hooks.py with a simple decorator or wrapper, log_tool_call(func), that wraps any tool function and logs to console (and optionally a file, tool_calls.log) with: timestamp, tool name, arguments (truncated if long), and how long the call took. Apply this hook to run_static_scan, search_memory, search_web, and save_finding.

2. Create app/agent.py with:
   - A tool registry: a list of tool definitions in OpenAI-compatible function-calling format (name, description, parameters schema) for the 4 tools: run_static_scan, search_memory, search_web, save_finding.
   - A review_code(path: str) function that implements the planner loop:
     a. Run the static scanner first (always, it's the fastest, most concrete signal)
     b. For each finding, ask the LLM (via chat() with tools passed in) whether it wants to search memory for similar past issues, search the web for more context, or has enough to report already, let the LLM genuinely decide via tool calls, don't hardcode the sequence
     c. Handle the LLM's tool call responses, execute the actual corresponding Python function, feed the result back to the LLM, repeat until the LLM produces a final answer instead of another tool call
     d. Return a structured final report: for each finding, the original scan result, any matched memory lesson (with similarity/confidence), any web context found, and the LLM's synthesized explanation and suggested fix

Keep the loop simple, a basic while loop checking if the LLM's response contains tool calls or a final message, cap it at a reasonable max iterations (e.g. 6) to avoid infinite loops.

Don't wire this into the CLI yet, that's the next step, just get review_code() working when called directly, and show me a test run's full output including the hook's logged tool calls.
```

**Result:** Worked end to end on a first test (a deliberate subprocess shell=True file), all three tools fired in sequence with the hook logging timestamps and durations for each. One real snag: Groq's tool-call output failed to parse correctly on the first attempt, had to fall back to Gemini for that smoke test. Known reliability quirk with some providers' function-calling output, added a retry that recovers when it happens rather than failing outright.

---

## 5. Verifying memory actually discriminates between bug types, not just noise

**Tool:** Cursor (Auto/Composer)
**Goal:** The first test (shell=True) only proved the loop runs end to end, it didn't prove memory matching is actually meaningful, that bug type isn't even in my seeded lessons. Needed a real test against one of my own actual bugs.

**Prompt used:**

```
Test review_code() against a file that reproduces the actual pattern from lesson-1 (cross-user read access without an ownership check), something like a function that fetches a resource by id alone, no check that the requesting user owns it.

Run it and show me the full report: does search_memory correctly surface lesson-1 with meaningfully higher confidence than the earlier test's low-confidence match? Does the LLM's final explanation correctly reference the ownership-check pattern? Also confirm which provider (Groq or Gemini) handled this run and whether the tool-calling issue from before recurred.
```

**Result:** This is the test that actually mattered. The earlier shell=True run matched the wrong lesson at low confidence (0.358, distance 1.79), this IDOR-pattern test correctly matched lesson-1 with meaningfully better confidence (0.494, distance 1.02), and the LLM's explanation correctly named the missing ownership check as the issue. Also noticed Semgrep found nothing on this file, expected, since IDOR/ownership bugs are a logic-level issue, not something a static pattern-matcher can catch syntactically, the agent correctly fell back to an LLM-driven logic review in that case, which is a real, useful design decision, not just a gap being papered over.

---

## 6. CLI wiring and the sectioned report

**Tool:** Cursor (Auto/Composer)
**Goal:** Wire the full agent loop into the actual `review` command, replacing the scanner-only version from step 1.

**Prompt used:**

```
Wire review_code() into the secondpass CLI, replacing the scanner-only version of the review command from Step 1.

1. Update the `review <path>` command to call review_code() instead of run_static_scan() directly.
2. Display the final report using a Rich-formatted output: for each finding, show the scan detail, the matched memory lesson (with confidence), any web context used, and the LLM's explanation/fix, in clearly separated, readable sections, not a flat table this time since the report has more structure now.
3. Keep the hook's tool-call logging visible during the run (console output), so it's clear which tools fired and in what order, this is part of the Week 4 demo requirement.

After implementing, run `secondpass review <path>` end to end on the IDOR test file from the last step, confirm the full report displays cleanly in the terminal, not just as raw returned data.
```

**Result:** Full end-to-end run on the IDOR file, tools fired in the right order (scan, memory, web, save_finding), report rendered as clean separated panels instead of raw JSON, live tool-call log lines streamed during the run so it's visible which tools fired and why.

---

## 7. Fixing save_finding to not create near-duplicates

**Tool:** Cursor (Auto/Composer)
**Goal:** Noticed save_finding fired on the last test run even though it matched an existing lesson closely, worth checking whether that was correct judgment or just unconditional saving.

**Prompt used:**

```
Check the current save_finding behavior in the agent loop from the last IDOR test run: did the LLM call save_finding because it judged this a genuinely new/confirmed issue, or does the current prompt/tool setup cause it to call save_finding on every review regardless?

If it's unconditional, fix it: save_finding should only be called when the finding represents something meaningfully new or distinct from what's already in memory, not a near-duplicate of an existing lesson. Update the tool's description/instructions to the LLM to make this condition explicit (e.g. "only save a new finding if it doesn't closely match an existing lesson, or if it's a genuinely new variant worth remembering").

Test this by running review twice on the same IDOR file: confirm the first run saves the finding (or doesn't, if it already matched lesson-1 closely enough not to need saving), and the second run doesn't create a duplicate entry.

Also do a final pass: check the CLI output is clean when there are ZERO findings (nothing to review), and when Semgrep, memory, or web search individually fail (e.g. no internet, Tavily down) - confirm the whole review doesn't crash, it degrades gracefully and still shows what it did find.
```

**Result:** The prompt guidance alone was too soft, the LLM still tried to call save_finding even after matching lesson-1 closely. Fixed with two layers: a stronger tool description, and a hard distance-threshold guard inside save_finding itself (skips saving if distance to an existing lesson is below a threshold), so the dedup doesn't rely purely on the LLM behaving correctly. Ran the same IDOR file twice, first run correctly skipped saving (recognized as a near-duplicate of lesson-1), second run also skipped, memory count stayed at 5 both times, no duplicates. Also confirmed graceful degradation: empty file shows a clean "nothing to review" message with no crash, Semgrep failing falls back to logic review, web search failing still produces a report with an empty web section, nothing crashes outright anywhere in the chain.

**Review notes:** Good reminder not to rely on an LLM alone to enforce something that matters for data correctness, backing the soft prompt instruction with a hard, deterministic check in code is the right pattern when the LLM's compliance isn't guaranteed.

---

## 8. Adding git diff support, a gap I'd missed

**Tool:** Cursor (Auto/Composer)
**Goal:** Realized after finishing step 7 that the tool only ever supported reviewing a single file or folder path, the git-diff-based "review what I just changed before I commit" workflow, the whole reason this beats a blind full-file scanner, was decided early on but never actually got built. Caught this myself going back over the original scope.

**Prompt used:**

```
Add git diff support to secondpass. Add a --diff flag to the review command:

secondpass review --diff

This should:
1. Run `git diff --staged` (or unstaged if nothing's staged, your call, explain the choice) to get the actually changed lines/files in the current repo
2. Extract just the changed file paths from that diff
3. Run review_code() against those changed files only, not the whole repo
4. If a file is only partially changed, still analyze the whole file for context (Semgrep/logic review needs surrounding code), but only report on findings that fall within the changed line ranges, so the user isn't shown unrelated pre-existing issues in code they didn't touch

Keep the existing `review <path>` command working as-is for reviewing a specific file directly. --diff is an additional mode, not a replacement.
```

**Result:** Checks staged changes first (matches the actual pre-commit moment), falls back to unstaged if nothing's staged yet so the command still works mid-edit. Scans the whole file for context but only reports findings on added lines, plus file-level logic-review findings if the file was touched at all. Verified against a staged one-file test with a deliberate shell=True issue, correctly picked up staged mode, 1 file, reported the finding on the right line.

**Review notes:** This was the actual intended daily-use workflow from the start, decided on early in planning, then dropped somewhere in the build without me noticing until I went back over the original scope. Read through gitdiff.py specifically, parsing a unified diff to figure out which lines were actually added (not just which file changed) versus surrounding context lines is fiddlier than anything else in this project, worth understanding the shape of it (the +/- prefixes and @@ hunk headers) even without tracing every line.

---

Real intended usage:

```bash
git add .
secondpass review --diff
# fix anything flagged
git commit -m "..."
```

## 9. MCP server exposing review as a callable tool

**Tool:** Cursor (Auto/Composer)
**Goal:** Week 5's first requirement, expose the review capability as an MCP tool over stdio so it can be called from Claude Code or Cursor directly.

**Result:** One tool, review_code, with a path input and a diff flag rather than two separate tools, since it's the same underlying capability and one report shape, matches Week 5's "narrow, well-named tools" guidance. Had to move hook logging from stdout to stderr, since MCP's JSON-RPC protocol needs stdout to carry only clean protocol messages, any stray print statement would corrupt it. Verified with a standalone smoke test client before connecting a real MCP client.

**Review notes:** Chose stdio over HTTP transport since that's what local IDE clients (Claude Code, Cursor) actually use, connecting by spawning the server as a subprocess. HTTP would matter if this ever became a hosted service for multiple remote users, not needed for personal local use right now, and the transport layer is separate enough from the tool logic that adding HTTP later wouldn't require rebuilding anything.

---

## 10. Fix: Semgrep not found when Cursor spawned the server

**Tool:** Cursor (Auto/Composer)
**Goal:** MCP worked when I ran the server manually, but when Cursor spawned it, Semgrep couldn't be found, the venv's Scripts folder wasn't on the PATH in that spawned process.

**Result:** scanner.py now resolves semgrep.exe relative to the active Python interpreter's location instead of relying only on PATH. Also updated .cursor/mcp.json to prepend the venv Scripts directory to PATH as a backup. Verified against a real shell=True sample through the actual Cursor-spawned server, not just the manual smoke test.

---

## 11. Supervisor + worker multi-agent split

**Tool:** Cursor (Auto/Composer)
**Goal:** Week 5's second requirement, move from one flat planner loop to a supervisor routing to at least two worker sub-agents.

**Result:** Split into a Supervisor, a MemoryWorker (only calls search_memory and interprets the match), and a WebResearchWorker (only calls search_web and interprets relevance). Supervisor decides which worker(s) to route a finding to, collects their outputs, and synthesizes the final report, still calling save_finding with the same dedup gate as before. Hook logging updated to show which agent made each call, not just the tool name, so the trace shows real hand-offs. Public entry points (review_code, review_changed_files) didn't change, so the CLI and MCP tool needed no changes, this was purely an internal restructure.

**Review notes:** Ran the same IDOR test through the new structure and traced the full hand-off sequence, supervisor scans, falls back to logic review, routes to memory worker, routes to web worker, synthesizes, confirms the report quality matched the pre-refactor version exactly. Good proof the refactor didn't quietly change behavior.

---

## 12. Fix: logic-review fallback was manufacturing false positives

**Tool:** Cursor (Auto/Composer)
**Goal:** Noticed the logic-review fallback (used when Semgrep finds nothing) was flagging a vague RBAC/ownership concern on basically every file, even genuinely clean helper functions, not a real finding, just the LLM defaulting to always saying something.

**Result:** Added an explicit assessor step before the supervisor runs, the LLM now has to decide has_issues true or false, and the prompt explicitly forbids inventing vague generic concerns. Clean code now correctly returns no_issues: true instead of a fabricated finding. Tested three cases: the IDOR file still correctly flags the real missing ownership check, a genuinely clean helper function now reports no issues, and app/scanner.py itself (previously falsely flagged) now comes back clean too. Also deleted the 3 junk lessons that had been saved into memory from this false-positive behavior, memory is back to the original 5 real, curated lessons.

**Review notes:** This is a good example of catching a subtle but real problem, the fallback wasn't broken in the sense of crashing or erroring, it was working exactly as instructed, the instructions themselves just implicitly encouraged the LLM to always find something. Worth remembering: an LLM asked to review something for problems will often manufacture one rather than say "this looks fine," has to be explicitly told that a clean result is a valid, expected answer.

---

## What's built, mapped to both weeks' requirements

**Week 4:** research/review agent with a web-search skill (Tavily), memory (ChromaDB, seeded from 5 real security lessons, semantically verified to discriminate between bug types), a hook logging every tool call with timestamps, a file-read plugin (scanner + agent loop reading and reasoning over real source files), and a multi-hop demo (scan finds nothing on the IDOR file, falls back to logic review, checks memory, checks web, produces a synthesized fix, all chained in one review).

**Week 5:** MCP server exposing review_code as one tool over stdio, connected to and tested through Cursor, internal logic split into a Supervisor routing to a MemoryWorker and WebResearchWorker, and a tracing layer showing which agent made each tool call across the hand-offs.

---

# Phase 3 — broader review + reliability (Week 5 remaining → Week 8)

> Active plan: `Phase3_PRD.md` (approved 2026-07-27). Core problem from stress-testing: textbook patterns are caught, subtler variants are missed, and the LLM can invent findings with weak evidence. Phase 3 starts with schema, confidence gate, benchmark, persistence, and CLI progress — still mostly unwired from the live Security path until Week 6.

## 13. Structured Finding / ReviewResult schema (isolated)

**Tool:** Cursor (separate chat; Grok / Composer-class)
**Goal:** Shared Pydantic schema both Security and a future Architecture Worker will emit, without wiring into the existing Semgrep `Finding` TypedDict or the live pipeline yet.

**Prompt used:**

```
Read the current secondpass codebase first (especially app/scanner.py — it already
has a Finding TypedDict for Semgrep results; do NOT rename or replace that).

Task: add a shared Pydantic structured-output schema for Phase 3 review findings.
Both the existing security pipeline and a future Architecture Worker will use it.
Do NOT wire it into the scanner, agent, or supervisor yet — schema + unit tests only.

Requirements:
- New module: app/schema.py
- Use Pydantic v2
- Finding model fields (only these):
  - finding_type: str
  - evidence: str — required, non-empty after strip; empty/whitespace must raise ValidationError
  - confidence: int — ge=0, le=100
  - suggested_fix: str
  - detection_method: Literal/Enum of "static_rule" | "llm_reasoning"
- ReviewResult model: list[Finding] + file_path: str, timestamp: datetime, worker_name: str
  (empty findings list is allowed)
- Add pytest to requirements if missing
- Unit tests in tests/test_schema.py:
  1) valid Finding / ReviewResult constructs successfully
  2) empty evidence fails validation
  3) confidence outside 0–100 fails validation
- Keep minimal: no extra fields, no pipeline integration, no Architecture Worker

Match existing project style. Show me the files you create and how to run the tests.
```

**Result:** `app/schema.py` + `tests/test_schema.py`; `pydantic>=2` and `pytest` added to requirements. Scanner TypedDict left alone. All schema tests passed.

**Review notes:** Requiring non-empty `evidence` (including whitespace-only) is the whole point — forces grounding before a finding can exist as a typed object. Kept unwired on purpose so we could land schema + tests before touching the agent.

---

## 14. Confidence gate helper (threshold 80)

**Tool:** Cursor (separate chat; Grok)
**Goal:** Split findings into accepted vs needs_review by confidence, as a pure helper — human-approval checkpoint pattern without a third "verifier worker."

**Prompt used:**

```
Read app/schema.py and Phase3_PRD.md §4.1 (confidence-gated verification) first.

Task: implement the confidence gate as a pure helper — do NOT wire it into the
CLI, agent, supervisor, or memory yet.

Requirements:
- New module: app/confidence_gate.py
- Default threshold: 80 (constant, easy to change later)
- Input: ReviewResult (from app.schema)
- Split findings into two lists:
  - accepted: confidence >= threshold → included in report as-is
  - needs_review: confidence < threshold → flagged for human review
- Return a small Pydantic model, e.g. GateResult, with:
  - accepted: list[Finding]
  - needs_review: list[Finding]
  - threshold: int
- Preserve finding order within each list
- Empty ReviewResult → both lists empty (valid)
- Unit tests in tests/test_confidence_gate.py:
  1) finding at threshold (80) goes to accepted
  2) finding below threshold (e.g. 79) goes to needs_review
  3) mixed ReviewResult splits correctly
- Keep minimal: no CLI, no persistence, no Architecture Worker

Match existing style. Show files created and how to run tests.
```

**Result:** `app/confidence_gate.py` (`DEFAULT_THRESHOLD=80`, `apply_confidence_gate` → `GateResult`) + tests for edge/mixed/empty. Still unwired.

**Review notes:** `>= 80` (not `>`) so the threshold is inclusive. Order preserved within each bucket — matters once we show a needs-review queue in the UI.

---

## 15. Detection-quality benchmark suite + evaluator stub

**Tool:** Cursor (separate chat; Grok)
**Goal:** Formalize planted-bug + clean fixtures and a precision/recall evaluator so reliability claims become measurable. `demo_notes/` was gone from the repo, so fixtures were recreated under `benchmark/`.

**Prompt used:**

```
Read Phase3_PRD.md §3 (known issue / demo_notes findings) and §4.1 (Detection-quality
benchmark). Also read app/schema.py so ground-truth labels can map to finding_type
later. demo_notes/ is currently MISSING from the repo — recreate a minimal version
as part of this task.

Task: build a versioned detection-quality benchmark suite. Do NOT run the full agent
pipeline or wire into CLI/MCP yet — fixtures + ground truth + a tiny evaluator stub
that can score precision/recall when given predicted findings.

Requirements:
- Recreate fixtures under benchmark/fixtures/ (or demo_notes/ if you prefer that name —
  pick one and stay consistent):
  - At least 2 bug classes, e.g. IDOR/missing-ownership AND command injection (shell=True)
  - At least 1 clean file that should produce zero findings
  - Keep files small and textbook-obvious (PRD: current detector handles textbook cases)
- Ground truth file, e.g. benchmark/ground_truth.json:
  - per fixture file: list of expected issues with finding_type, and optionally
    approximate location (path + line or symbol name)
  - clean files: empty expected list
- Evaluator stub: app/benchmark.py or benchmark/evaluate.py
  - Input: predicted findings (list of {file_path, finding_type, ...}) + ground truth
  - Output: precision, recall, true_positives, false_positives, false_negatives
  - Matching rule: same file_path + same finding_type counts as a hit (keep matching
    simple for v1; no fuzzy text matching)
- Unit tests for the evaluator only (synthetic predictions vs ground truth), not live LLM calls
- Keep minimal: no Semgrep/LLM invocation in this PR, no Architecture Worker, no wiring
  into agent/supervisor

Match existing style. Show files created and how to run tests.
```

**Result:** `benchmark/fixtures/` (IDOR, shell=True, clean ownership), `benchmark/ground_truth.json`, `app/benchmark.py` (`evaluate` → `ScoreReport`), `tests/test_benchmark.py`. Synthetic predictions only.

**Review notes:** v1 matching is deliberately coarse (file + finding_type). That's enough to track before/after Phase 3 changes; line-level matching can come later if false credit becomes a problem.

---

## 16. SQLite persistence for review history + verified outcomes

**Tool:** Cursor (separate chat; Grok)
**Goal:** Persistent store for review runs and human accept/reject outcomes — separate from ChromaDB lesson embeddings. Storage API only; no FastAPI yet.

**Prompt used:**

```
Read Phase3_PRD.md §4.1 (Verified-outcome memory + Web frontend/backend persistence notes),
app/schema.py, and app/confidence_gate.py. Also skim app/memory.py so you don't
duplicate or conflict with ChromaDB lesson memory — this step is a SEPARATE
persistence layer for review history + verified outcomes.

Task: set up the persistence schema/models and a thin storage API only.
Do NOT build FastAPI endpoints, dashboard, or wire into the agent yet.

Requirements:
- New module(s), prefer app/persistence.py (and optionally app/db.py if needed)
- Use SQLite via stdlib sqlite3 (or SQLAlchemy only if clearly simpler — prefer stdlib)
- Store:
  1) Review runs: id, file_path, worker_name, created_at, full ReviewResult JSON,
     gate threshold, accepted/needs_review counts (or store GateResult JSON)
  2) Verified outcomes (PRD memory schema): finding JSON, accepted (bool), reason (str),
     linked_fix_commit (optional str), related review_id and/or file_path,
     created_at
- Organize retrieval by repo/file_path (helper: list_outcomes_for_file(file_path))
- Functions: init_db(), save_review(...), get_review(id), list_reviews(limit=...),
  save_verified_outcome(...), list_outcomes_for_file(...)
- Default DB path: .secondpass/secondpass.db (create dir if missing); allow override
- Unit tests with a temp DB path (no shared .chromadb / no real agent runs)
- Keep minimal: no auth, no FastAPI, no CLI commands, no Architecture Worker

After implementing, in your summary briefly explain (for my learning, keep it short):
- What each new file is for
- The main control flow / data flow in 3–6 bullets
- One limitation or intentional non-goal of this step
Instead of adding long comments into the code itself, put that explanation in the reply.

Match existing style. Show files created and how to run tests.
```

**Result:** `app/persistence.py` (reviews + verified_outcomes, path-normalized file retrieval), `tests/test_persistence.py`, `.secondpass/` gitignored. Still unwired from agent/CLI.

**Review notes:** Two memories on purpose: ChromaDB = semantic lessons for retrieval during review; SQLite = audit/history + human-verified outcomes. Don't merge them until there's a deliberate "accepted finding → new lesson" path.

---

## 17. CLI review progress / loading states

**Tool:** Cursor (separate chat; Grok)
**Goal:** Spinners + colored stage labels during long reviews so the CLI feels alive, without changing review semantics or MCP behavior.

**Prompt used:**

```
Read app/cli.py and how review_code / review_changed_files are invoked today.
Also skim app/agent.py enough to know the main stages of a review (scan → optional
logic review → supervisor/workers → report).

Task: add CLI progress / loading states for the review command so long runs feel
alive (spinners + colored status), without changing review semantics.

Requirements:
- Use Rich (already in the project) for spinner/status output
- Show clear stage messages during a review, e.g.:
  - scanning with Semgrep
  - logic review (when that path runs)
  - routing / workers
  - building report
- Keep output readable when the final Rich report prints (no leftover spinner junk)
- Prefer a small helper in app/cli.py (or app/progress.py if cleaner) rather than
  scattering prints through the agent
- If the agent today doesn't expose stage callbacks, add the minimal hook needed
  (e.g. optional on_stage callback / context) — keep the change small
- Instead of wiring persistence, schema migration, confidence gate display, or
  Architecture Worker in this step, leave those for later
- Instead of adding long comments in the code, put a short learning summary in
  your reply: what each touched file is for, control flow in 3–6 bullets, and one
  intentional non-goal of this step
- Add a light unit/smoke test only if there's pure helper logic worth testing;
  don't try to mock the full LLM/Semgrep pipeline

Match existing CLI style. Show files changed and how to manually demo
`secondpass review <path>` with the new progress output.
```

**Result:** `app/progress.py` (`ReviewProgress` + stage labels); optional `on_stage` on `review_code` / `review_changed_files`; CLI owns spinner lifecycle; MCP unchanged when callback omitted; `tests/test_progress.py` for labels only.

**Review notes:** Optional callback was the right seam — CLI gets UX, MCP/tests stay quiet. Stages are emitted at real pipeline boundaries, not fake timers.

---

## 18. Finalize Phase 3 architecture diagram (§4.4)

**Tool:** Cursor (separate chat; Grok)
**Goal:** Update the PRD architecture section so it honestly maps LIVE vs BUILT vs PLANNED after Week 5 foundation landed. Design doc only.

**Result:** `Phase3_PRD.md` §4.4 rewritten with status legend, live path diagram, built-but-unwired table, target Security + Architecture pipeline (schema → gate → verified outcomes → audit trail), surface rules (CLI/MCP triggers only), and Week 6–7 wiring order. Week 5 checklist fully checked off. No code changes.

**Review notes:** The useful part is the wiring order: (1) Security emits `ReviewResult` + gate, (2) Architecture Worker, (3) persist + verified outcomes, (4) extended audit trail, (5) optional browse-only dashboard. That sequence keeps Week 6 from trying to do everything at once.

---

## 19. Wire Security path to schema + confidence gate

**Tool:** Cursor (separate chat; Claude / GPT Sol–class)
**Goal:** First Week 6 wiring step — live Security reviews emit schema-valid findings and split accepted vs needs_review, without building Architecture Worker yet.

**Result:** Mapping helpers in `app/agent.py` (`map_semgrep_finding`, `map_logic_issue`, `build_security_review_output`); workers still see scanner-shaped dicts and get a parallel `structured_finding`; gate at 80; CLI shows accepted vs needs-review; MCP JSON documents the new shape; `findings` kept as accepted alias for compatibility. Semgrep → `static_rule` @ 90; logic → `llm_reasoning` with model confidence or 70 default. Offline tests in `tests/test_security_schema_wiring.py`. 25 tests passing.

**Review notes:** Keeping the internal scanner dict for Memory/Web was the right compatibility choice — schema rides alongside instead of forcing a worker rewrite. Persistence still not writing reviews yet (Week 7).

---

## 20. Architecture Worker + cross-file context

**Tool:** Cursor — GPT 5.6 Sol (Security schema wiring, §19); Claude Sonnet 5 (this Architecture Worker step)
**Goal:** Second worker for naming/layering/dependency-direction/duplication, with deterministic cross-file context, same schema + gate as Security.

**Result:** `app/context.py` gathers related files (imported_by_target → same_package → imports_target), first-party only, capped (6 files / 2k chars each / 8k total). `app/workers/architecture_worker.py` one-shot LLM review (no tool loop). `review_architecture` + `review_path` in agent; CLI prints Security then Architecture panels with accepted/needs-review. Diff mode and directories skipped in v1. 44 tests passing.

**Review notes:** Not a unified Supervisor router yet — `review_path` runs Security then Architecture sequentially. That's honest for v1; a real aggregation/routing layer is still an open Week 6 item if we need one report object instead of two. Confidence for architecture/logic findings is still LLM self-reported (clamped), Semgrep is hardcoded 90 — not calibrated; gate at 80 is a UX triage aid until verified-outcome data exists.

**Manual check (2026-07-28) — AI output wrong, corrections not applied to code:** Ran `python -m app.cli review app/agent.py`. Security path correctly reported clean (Semgrep empty → logic review clean). Architecture pulled 5 related files and flagged 2 issues, both accepted at the gate (≥80):

1. `duplicated_logic` @ 90% on `map_semgrep_finding` / `map_logic_issue` — soft smell, not a real defect. The functions look similar because both build a `StructuredFinding`, but inputs and detection_method/confidence differ; shared helpers (`_finding_evidence`, `_bounded_confidence`) already exist. Did **not** extract a shared mapper.
2. `naming_convention` @ 80% on `_LOGIC_ASSESS_SYSTEM`, suggesting rename to `LOGIC_ASSESS_SYSTEM` — **false positive**. Leading `_` + SCREAMING_SNAKE is intentional module-private style in this repo (same as `_STATIC_RULE_CONFIDENCE`, `_MAX_LOGIC_SOURCE_CHARS`). The worker invented a convention that isn't ours. Did **not** rename.

Takeaway: Architecture is noisier than Security on "conventions" smells; LLM confidence alone put weak/false findings into the accepted bucket. Next step should harden the Architecture Worker prompt (and soft-smell confidence bias) before treating architecture output as trustworthy — separate from the Security benchmark eval.

---

## 21. Harden Architecture Worker against logged false positives

**Tool:** Cursor Grok 4.5
**Goal:** Stop soft `duplicated_logic` / invented `naming_convention` findings from landing in the accepted bucket on `app/agent.py` (see §20 manual check). Schema + gate threshold 80 unchanged.

**Result:** Tightened `_SYSTEM` in `architecture_worker.py` (visible-pattern naming only; mild similarity = clean; soft smells bias &lt;80). Deterministic post-process: `is_soft_only_smell` drops soft FP language; `adjust_architecture_confidence` caps `naming_convention` / `duplicated_logic` to 79 unless hard-evidence markers. Offline tests updated. Gate threshold left at 80.

**Manual re-check (2026-07-29):** Re-ran `python -m app.cli review app/agent.py` twice after the harden.

- First run: Semgrep failed DNS to `semgrep.dev` (`getaddrinfo failed` / Max retries on `/c/p/python`) — network blip, not an agent logic bug. Pipeline continued: logic-review clean, architecture clean. CLI printed a huge Semgrep traceback in the security header (noisy UX; worth softening later).
- Second run: Semgrep ok (empty), logic clean, architecture clean — **neither of the two prior Accepted findings returned.** Matches expectation.

**Review notes:** Prompt tightening alone is soft; the drop/cap helpers are the hard enforcement (same pattern as §7 near-duplicate guard — don't trust the LLM to obey when correctness matters). Semgrep rule packs (`p/python`, `p/javascript`) still download from the network on cache miss — offline/DNS failure surfaces as ScanError then logic fallback. Architecture harden ≠ Security harden checklist item; Security benchmark eval is still next.

---

## 22. First Security benchmark evaluation pass (live runner)

**Tool:** Cursor (Grok 4.5)
**Goal:** Measure real Security-path precision/recall on the planted-bug suite — evaluator already existed (`app/benchmark.py`); this step adds the runner that feeds it live `review_code` output.

**Result:** `app/benchmark_run.py` + `tests/test_benchmark_run.py`. Live baseline 2026-07-29: precision **1.0**, recall **1.0** (2 TP / 0 FP / 0 FN) on accepted findings. Per fixture: clean stayed clean; `notes_idor` → `missing_ownership_check` (logic fallback); `ops_shell` → Semgrep shell-true mapped to `command_injection` via `SEMGREP_TO_BENCHMARK_TYPE`. Results: `benchmark/results/baseline_20260729.json`. `--offline` Semgrep-only would FN the IDOR fixture (recall 0.5) — expected.

**Review notes:** Perfect score on a 3-file _textbook_ suite is a smoke baseline, not proof the tool is “done.” PRD §3 already said subtle variants can miss and clean code can FP — those cases aren’t in ground truth yet. Expanding fixtures later is how you make the number meaningful; stacking Bandit before you have a miss to explain would be checklist-driven, not evidence-driven. Architecture harden (§21) left untouched.

---

## 23. Supervisor path-level aggregation (Security + Architecture)

**Tool:** Cursor (Grok 4.5)
**Goal:** Satisfy Awais’s “Supervisor routes and aggregates” — one entry point runs both top-level workers and merges counts into a combined report. Keep Memory/Web `supervise_finding` inside Security.

**Result:** `supervise_review` + `aggregate_worker_reports` in `app/supervisor.py`; `review_path` / CLI `review <path>` call Supervisor once; summary panel then Security/Architecture panels; offline aggregation tests. Gate threshold 80 unchanged. `--diff` still Security-only.

**Manual check (2026-07-29):** `review benchmark/fixtures/notes_idor.py` twice. Security correctly finds IDOR (`missing_ownership_check`), memory lesson-1 + web context. Supervisor summary shows workers + overall counts. Architecture still sometimes re-labels the same ownership bug as `layering_violation` (and once a soft `dependency_direction` on using `NOTES`) at ≥80 — **category bleed / duplicate of Security**, not a real architecture finding. Log for a later Architecture harden (forbid security/authz issues; those belong to Security Worker). Did not change fixtures. Week 6 mandatory Supervisor item done.

**Non-determinism observed (2026-07-29, flagged by external review, not caught by me at the time):** The two back-to-back `notes_idor.py` runs above didn't just repeat — they diverged. Run 1: 3 accepted findings (security 1 + architecture 2, including `dependency_direction` at exactly 80%, right at the gate line). Run 2: 2 accepted findings (security 1 + architecture 1); `dependency_direction` vanished entirely; the surviving `layering_violation` confidence shifted 95% → 90%. Same file, same code, different score and different accepted count. This is expected given LLM sampling + self-reported confidence (not seeded/deterministic), but it was never logged or investigated at the time — should have been. Real risk: a finding sitting exactly at the gate threshold (80%) can flip accepted/needs_review between runs on identical input. Follow-up: run one fixture 5–10x and record the confidence spread before citing "reproducible" to the panel.

**Correction to §23 checklist entry — verified, not just asserted (2026-07-30):** The PRD's "benchmark eval: done, 1.0/1.0" claim up to this point rested on a pasted agent summary from the 2026-07-29 run, not on me independently re-running it. Caught by external review as the same "trust the summary, don't check the number" pattern that caused problems before. Re-ran `python -m app.benchmark_run --label verify_20260730` myself on 2026-07-30 and read the raw output directly: `ScoreReport: {'true_positives': 2, 'false_positives': 0, 'false_negatives': 0, 'precision': 1.0, 'recall': 1.0}` across all 3 fixtures (clean/IDOR/shell), each with visible per-file routing/memory/web logs, written to `benchmark/results/verify_20260730_20260730.json`. The original baseline number was correct. Lesson: independently re-run and read raw tool output for any claim used to justify skipping/deferring a checklist item (here: "no misses to harden against") — don't accept a paraphrased result as verification.

**Confidence variance measurement (2026-07-30):** Ran `python -m app.cli review benchmark/fixtures/notes_idor.py` 6x back-to-back, same file, same code, no changes in between. Overall `accepted` count: **2, 2, 1, 2, 3, 2** (range 1–3 across 6 runs). Confidence values observed on the recurring finding: **95, 100, (none — architecture reported nothing that run), 90, 80, 95** (range 80–100). Run 5 was the sharpest case: four separate findings landed at exactly 80%, the gate threshold, simultaneously — meaning whether a finding is "accepted" vs "needs review" can flip based on LLM sampling noise alone, not on the underlying code changing. This is expected (confidence is LLM self-reported, not calibrated/seeded — see §20 review notes) but is now a real, citable number instead of a guess: **on this fixture, confidence varies roughly ±10–20 points and accepted-finding count varies ±1 run to run.** Answer for "is this reproducible": no, not at the individual-finding level; yes, at the "real bug gets flagged at all" level (all 6 runs caught the IDOR). Worth a caveat in any benchmark report and a candidate reason to eventually explore confidence calibration or majority-vote sampling, not fixed now.

---

## 24. Pin LLM temperature=0 for review calls (variance experiment)

**Tool:** Cursor (Grok 4.5)
**Goal:** Isolate sampling noise as the cause of confidence/accepted-count variance before blaming Groq model quality or swapping providers. One variable only.

**Result:** `app/llm.py` — `chat(..., temperature=None)` resolves to `0.0`; if provider rejects `temperature=0`, retries `0.01`, then omits the field. Call sites (logic-review, Architecture, Supervisor route/synth/save, Memory/Web via `run_tool_loop`) pass `temperature=0`. Offline tests in `tests/test_llm.py` (4 passed). Schema/gate unchanged.

**Gap (caught on review, not in the implementer's summary):** fallback is silent — no `log_agent_event` / stderr when Groq rejects 0 and we retry 0.01 or omit. So we cannot prove from a live run log that temperature=0 was actually accepted vs quietly fallen back. For Groq + llama-3.3-70b in practice, 0 is usually accepted (no BadRequestError in these runs), so the after-measurement is still meaningful, but the observability hole should be a one-line follow-up.

**After re-measure (2026-07-30, same fixture, ≥4 runs with temp=0 pinned):**

|                            | Before (default temp)            | After (temperature=0)                                                                                                          |
| -------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Overall accepted           | 2, 2, 1, 2, 3, 2 (range **1–3**) | 2, 2, 2, 2 (range **2** — much tighter)                                                                                        |
| Security finding type      | IDOR (when present)              | Always `missing_ownership_check` @ **100%**                                                                                    |
| Architecture finding types | mixed / sometimes absent         | Almost always `layering_violation` @ **90%** (ownership bleed); one run also soft `naming_convention` @ **79%** → needs_review |
| Confidence spread          | 80–100                           | Security stuck at 100; Architecture layering stuck at 90                                                                       |

**Verdict:** Temperature pin **did** kill most of the accepted-count thrash. Remaining variance is mostly Architecture inventing soft extras (naming), not Security bouncing. **Do not escalate to OpenAI/GPT yet** — the sampling hypothesis was largely confirmed; provider swap would muddy the experiment. Category bleed (Architecture still re-labeling ownership as `layering_violation` every run) is still open — the dedicated authz-exclusion harden (prompt B) has **not** landed yet; only the older soft "do not flag security" line exists, which is clearly insufficient.

**Humility note (keep this sharp for the panel):** lower temperature reduces run-to-run noise; it does **not** make self-reported confidence calibrated or "true." A model can still be confidently and consistently wrong (Architecture @ 90% on a security bug is exactly that). Gate threshold / multi-call averaging remains an open design question, not answered by this experiment.

---

## 25. Temperature fallback logging + Architecture authz-bleed harden

**Tool:** Cursor (Grok 4.5)
**Goal:** (A) Prove temperature=0 is actually used, not silently fallen back. (B) Stop Architecture from re-labeling ownership/IDOR as `layering_violation` / ownership-flavored naming — with two-sided tests so the filter doesn't eat real architecture findings.

**Result:**

- **A:** `chat()` logs `llm temperature: using 0.0 (requested 0.0)` on success, or reject → `fell back to 0.01` / `omitted`. Offline tests cover both success and fallback paths.
- **B:** Prompt forbids auth/ownership/IDOR/access-control + security-flavored renames (`NOTES_WITHOUT_OWNERSHIP_CHECK`). Code filter `is_security_category_bleed()` drops those even if the LLM still emits them. Two-sided tests: IDOR layering + ownership naming dropped; genuine `dependency_direction` / hard `duplicated_logic` (importing upward / copy-pasted blocks — not ownership word-swaps) survive. 27 offline tests passed.

**Independent verify (2026-07-30) — A and B checked separately, not as one bundled "done":**

1. **A (logging):** Live CLI run shows multiple `[agent] … llm temperature: using 0.0 (requested 0.0)` lines (logic-review, routing, memory, web, architecture). No reject/fallback lines → Groq accepted real `0.0` for this provider/model. §24's "probably 0" inference is now observed, not assumed.
2. **B (bleed):** Same run — Security accepted `missing_ownership_check` @ 100%; Architecture reported **clean** (0 accepted / 0 needs review). Supervisor overall accepted=1. The previous consistent `layering_violation` @ 90% about ownership is gone. Offline survive tests assert different finding types (`dependency_direction`, hard duplication), not ownership examples with synonyms.

**Review notes:** Prompt + hard filter again (same lesson as §21 soft-smell harden). Category-bleed fix unblocks clean accept/reject testing for Week 7 verified memory — Architecture no longer polluting the queue with Security duplicates on this fixture.

---

## 26. Wire verified-outcome memory; disable Chroma auto-save

**Tool:** Cursor (separate chat)
**Goal:** Awais objective #3 — durable memory only from human accept/reject + reason. Stop Supervisor from inventing Chroma lessons.

**Result:** Removed Supervisor `save_finding` tool loop (logs `supervisor skip save_finding — verified outcomes require human accept/reject`). `supervise_review` persists each worker `ReviewResult` via `save_review`. CLI: `decide`, `list-reviews`, `list-outcomes`. Helpers in `app/verified.py`. Chroma still retrieves seed lessons only. Offline tests in `tests/test_verified_outcomes.py` (4 passed).

**Manual verify (2026-07-30):** Review of `notes_idor.py` showed skip-save line + `Persisted review ids: security=3, architecture=4`. `list-reviews` showed those rows. `list-outcomes` empty before `decide` (expected). Architecture stayed clean.

**Chroma audit (2026-07-30, checked directly — not assumed):** `init_memory().count() == 5`, IDs exactly `lesson-1`…`lesson-5`, matching `security_lessons.json` with **zero extras**. No residue from the Weeks 5–6 auto-save window. Seed retrieval is still trustworthy.

**UX note:** Interactive accept/reject prompts/buttons deferred — live with explicit `decide` first; richer UX fits Week 7 dashboard / optional later CLI polish (`questionary`), not today.

---

## 27. FastAPI async submit → poll → result

**Tool:** Cursor (separate chat)
**Goal:** Week 7 backend — HTTP surface for submit/history/outcomes without blocking the event loop; align PRD with Awais (“submit a review” allowed via API).

**Result:** `app/jobs.py` (in-memory jobs + `ThreadPoolExecutor`); `app/api.py` (POST/GET reviews, job poll, outcomes); fastapi/uvicorn/httpx in requirements. §4.4 updated: API may submit scans; CLI/MCP primary. Offline `tests/test_api.py` (4 passed) includes poll-while-`running`.

**Independent verify (2026-07-30):** Live server demo — POST returned immediately; polled `status=running` ~10 times while Semgrep/LLM ran; then `completed` with `persisted_review_ids.security=7`; GET review + POST/GET outcomes worked. Proves thread-pool offload, not a blocking BackgroundTasks trap.

---

## 28. End-to-end integration tests (API → gate → persist → outcomes)

**Tool:** Cursor (separate chat)
**Goal:** Prove the full Week 7 chain in one automated path, not piece-by-piece unit coverage.

**Result:** `tests/test_integration_review_flow.py` — happy accept, needs_review→reject, clean file, both workers persist. Only Semgrep/LLM/seed/web mocked. 4/4 pass on independent re-run.

**Seam caught (first run, test 4):** Architecture mock `suggested_fix` contained “authorization” → live `is_security_category_bleed` dropped every issue → Architecture persisted clean (`accepted_count=0`) while the LLM claimed `has_issues=true`. Unit tests mocking the whole architecture runner never hit LLM text → bleed filter → gate → SQLite. Fixed by rewriting the mock to pure layering language. Citable panel line: integration tests found a cross-piece seam the unit suite missed; product filter behaved correctly, mock dialect was wrong.

**Review notes:** Persist/decide operate on full `review_result.findings`, so below-threshold items stay decideable. Integration mocks must speak production dialect (OpenAI message shape + architecture text that won’t trip security-bleed).

---

## 29. Persistent audit trail keyed by job_id

**Tool:** Cursor (separate chat) + independent live verify
**Goal:** Queryable “why trust this” trail for one submission spanning Security + Architecture — not two half-trails by worker review_id, and not only the flat `tool_calls.log`.

**Result:** `audit_events` table in SQLite; `app/audit.py` (ContextVar scopes, redacted prompt summaries); wired through `supervise_review(job_id=…)`; `GET /reviews/jobs/{job_id}/audit` + `secondpass audit <job_id>`. Offline `tests/test_audit_trail.py` asserts security schema before architecture schema.

**Live verify catch (2026-07-31) — would have marked “done” wrongly:** First live API submit failed with `OperationalError: no such column: job_id`. Cause: `_SCHEMA_SQL` ran `CREATE INDEX … ON reviews(job_id)` via `executescript` *before* `_migrate_schema` could `ALTER TABLE … ADD COLUMN job_id` on pre-existing DBs; migration never ran. Temp-DB unit tests never hit this. Fix: remove that index from the bootstrap script; create it only after migrate. Added `test_init_db_migrates_pre_job_id_reviews_table`.

**Live verify after fix:** `job_id=11d09fd7-…`, 17 events, one sequence: `review_start` → security `prompt_io` / `schema_validation` / `confidence_gate` → architecture `prompt_io` / `schema_validation` / `confidence_gate` → both `review_persisted` → `review_complete`. Security schema/gate ids precede architecture’s. `VERIFY_OK` on the strong order check, not just “both workers appear.”

---

## 30. Expand benchmark: Architecture gets a real scored suite; Security diversifies bug classes

**Tool:** Cursor (separate chat)
**Goal:** §21/§23/§25 hardened Architecture against false positives but never scored it — only manual spot-checks. Also Security's 1.0/1.0 baseline was 2 bug classes (IDOR, command injection); too narrow to mean much.

**Result:**

- Security: added `hardcoded_secret.py` and `path_traversal.py` fixtures + `ground_truth.json` entries. Neither triggers a Semgrep rule (`p/python`, 0 findings, checked directly) — both go through the LLM logic-review fallback, same path as `notes_idor.py`. Probed live with `temperature=0` before committing labels: model returned exactly `hardcoded_secret` / `path_traversal` at confidence 100, matching intended ground truth. `app/benchmark_run.py` untouched (Security Worker code, gate, schema untouched per scope); only fixtures + ground truth grew.
- Architecture: new `benchmark/fixtures/architecture/` subdirectory (kept separate from Security's fixtures so `app/context.py` same-package sibling-gathering doesn't mix the two suites) — `checkout_handler.py` (layering violation: bypasses a service layer to mutate a data-layer dict directly), `low_level_persistence_client.py` (dependency-direction violation: a "low-level" module imports and calls back into a high-level orchestration module), `clean_price_formatter.py` (clean). New `benchmark/ground_truth_architecture.json` + parallel `app/benchmark_run_architecture.py` (reuses Security's `predictions_from_report_items` / `normalize_benchmark_finding_type` — Architecture's finding_type values already match ground-truth labels, no remapping needed). `app/benchmark_run.py` not modified.
- Existing `tests/test_benchmark_run.py` needed two number updates (not logic changes) because they scored against the now-larger default `ground_truth.json`: `test_mapped_predictions_score_against_real_ground_truth` scoped to its original 2 fixtures explicitly; `test_run_benchmark_offline_writes_results` recall expectation updated 0.5 → 0.25 (3 FN now, not 1, since offline/Semgrep-only can't catch the 2 new LLM-fallback classes — expected, not a regression).

**Live scores (2026-07-31):**

| Worker | Fixtures | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|---|
| Security | 5 (clean, IDOR, shell, secret, traversal) | 4 | 0 | 0 | **1.0** | **1.0** |
| Architecture | 3 (layering, dependency-direction, clean) | 2 | 2 | 0 | **0.5** | **1.0** |

Security stayed 1.0/1.0 — the two new bug classes were both textbook-obvious and matched the LLM's natural labeling on the first live probe, no ground-truth-label tuning needed.

**Architecture did NOT stay 1.0/1.0 — precision dropped to 0.5, reproduced identically on a second run (temp=0 pinning holding).** Root cause is real, not a flaky fixture: `run_architecture_worker` puts the target file's cross-file context siblings in the same prompt, and the model reports issues it can see in *any* context file, not just the target. Reviewing `checkout_handler.py` correctly finds its own `layering_violation` but *also* reports `dependency_direction` whose evidence text is plainly about the sibling `low_level_persistence_client.py` — and vice versa. Both fixtures are real bugs (recall 1.0, nothing missed), but each review over-attributes the *other* fixture's issue to itself, so scored-by-target-file precision is 0.5. This is a genuine cross-file-context seam PRD §4.1 flagged as needed ("cross-file context, not just the single file") but never stress-tested with two co-located bugs before. Not smoothing this over — it's the most useful number this benchmark expansion produced.

**Independent confirm (terminal, 2026-07-31):** Re-ran both evals myself-via-user transcript — Security `ScoreReport` 4/0/0 → 1.0/1.0 written to `security_expanded_20260731.json`; Architecture 2/2/0 → 0.5/1.0 in `architecture_baseline_20260731.json`, with per-file predicted lists exactly showing the sibling misattribution. Next step: target-file attribution filter (§31), same harden family as §21/§25 — fix before dashboard.

**Review notes:** Kept fixtures/ground truth additive and out of Security Worker/gate/schema/API/audit code per scope. hooks.py stays the live console stream; panel evidence is the SQLite trail. Prompt I/O stored as redacted summaries (role/length/240-char preview), not full text.

---

## 31. Architecture target-file attribution filter (cross-file leak)

**Tool:** Cursor (separate chat)
**Goal:** Fix §30 precision 0.5 — findings correctly saw both bugs but attributed the sibling's bug to the file under review.

**Result:** Prompt: context files are relationship evidence only, not extra subjects. Deterministic `is_off_target_finding` (same drop pattern as soft-smell / authz-bleed): drop if evidence names a context sibling without the target, unless a symbol defined in the target anchors it; else require path or target-symbol match. Two-sided offline tests on the real checkout / persistence fixtures + mocked mixed LLM payload. 26 architecture tests passed.

**After re-measure (2026-07-31, `architecture_attribution_fix`):**

| | Before (§30 baseline) | After (attribution filter) |
| --- | --- | --- |
| Precision | **0.5** (2 TP / 2 FP) | **1.0** (2 TP / 0 FP) |
| Recall | **1.0** (0 FN) | **1.0** (0 FN) |
| checkout predicted | layering + dependency (sibling leak) | layering only |
| persistence predicted | dependency + layering (sibling leak) | dependency only |
| clean | clean | clean |

**Independent verify (manual CLI + re-benchmark, 2026-07-31):** `checkout_handler` Architecture → only `layering_violation`; `low_level_persistence_client` → only `dependency_direction`; live ScoreReport 2/0/0 → 1.0/1.0 in `architecture_attribution_fix_20260731.json`. Matches the claimed table.

**Side observation (not fixed here):** full CLI `supervise_review` still runs Security on these architecture fixtures, and Security invents security-flavored labels for the same structural bugs (`bypass_of_business_rules`, `dependency_direction_violation` + irrelevant CWE-345 web context). Architecture attribution is fixed; the reverse bleed (Security over-claiming architecture fixtures) is a separate, optional harden — log only for now.

**Review notes:** Prompt alone is soft; the filter is the hard enforcement. Cross-file context stays — only wrong *attribution* is cut. Second measured before→after reliability win after §24 temperature pin.

---

## 32. Security reverse category-bleed harden (architecture-flavored logic-review)

**Tool:** Cursor (separate chat)
**Goal:** §31 side observation — Security inventing `bypass_of_business_rules` / `dependency_direction_violation` on Architecture fixtures with no real security issue.

**Result:** Logic-review prompt forbids architecture/business-rule/dependency findings. Deterministic `is_architecture_category_bleed` (mirror of Architecture's authz bleed) drops those labels; known security finding_types are allowlisted so IDOR/secret/traversal/shell still pass. Architecture fixtures added as expected-clean rows in Security `ground_truth.json` (standing FP check). Architecture runner separately reviews Security fixtures and asserts no authz-bleed findings (does not score them into Architecture precision). Offline `tests/test_security_architecture_bleed.py` + `app/benchmark_cross_worker.py`. 124 tests passed.

**After re-measure (2026-08-01):**

| | Before (§30/§31 Security on own suite) | After (reverse-bleed harden) |
| --- | --- | --- |
| Security precision | **1.0** | **1.0** |
| Security recall | **1.0** | **1.0** |
| Security on Architecture fixtures | invented architecture-flavored labels (§31) | **0 accepted / 0 needs_review** on all 6 |
| Architecture own-suite P/R | 1.0 / 1.0 (§31) | **1.0 / 1.0** (untouched worker) |
| Architecture authz-bleed on Security fixtures | N/A | **ok** (no ownership/IDOR labels); note: Architecture may still emit non-authz `layering_violation` on `ops_shell`/`path_traversal` — out of scope here |

Results: `security_arch_bleed_fix_20260801.json`, `architecture_after_security_bleed_fix_20260801.json`.

**Review notes:** Same harden family as §21/§25/§31 — prompt + hard drop filter + standing eval. Do not fold Security fixtures into Architecture GT as expected-clean (that conflates Architecture precision with a different failure mode).

---

## 33. Architecture insufficient-structure harden (stdlib-as-layer invention)

**Tool:** Cursor (this chat)
**Goal:** Architecture inventing `layering_violation` @ ≥80 on tiny Security fixtures (`ops_shell`, `path_traversal`) by treating stdlib imports (`subprocess`, `pathlib`) as layer boundaries — different from §21 soft smells and §25 authz bleed (not relabeling a real bug; inventing a rule from nothing).

**Provider note:** Live evidence on **gpt-4o-mini** (full finding text). Same labels already appeared under **Groq** in `architecture_cross_worker_20260801.json` (ops_shell + path_traversal → `layering_violation`, FP=2 when scored). Fresh Groq re-check blocked by TPD 429 this session — treat as provider-agnostic prompt/filter gap, not a gpt-only quirk.

**Result:** Prompt section `INSUFFICIENT STRUCTURE`: stdlib import ≠ layering/dependency evidence; no identifiable project layer → `has_issues=false` for those claim types. Deterministic `is_insufficient_structure_claim`: if the target source has **no first-party imports** (only stdlib / none), drop `layering_violation` / `dependency_direction` — structural surface check, not a growing phrase blocklist (models were inventing "service layer" in suggested_fix to dodge text markers). Real architecture fixtures keep first-party imports → survive. Offline two-sided tests in `tests/test_architecture_worker.py` (31 passed). Also added native `openai` provider in `app/llm.py` for cross-provider checks.

**Manual verify (2026-08-01, openai/gpt-4o-mini):**
- `ops_shell.py` / `path_traversal.py` → Architecture **0 accepted** (LLM still claims issues; filter drops them)
- `checkout_handler.py` → `layering_violation` kept
- `low_level_persistence_client.py` → real upward-dependency finding kept

**Review notes:** Third distinct reliability mode: soft smell (§21) / category bleed (§25/§32) / invented structure on files with no layers (§33). Fix is "recognize insufficient surface," not another keyword list.

---

## 34. Failure-mode harden (LLM 429 + Semgrep network noise)

**Tool:** Cursor (build chat) · **Date:** 2026-08-02 / verified 2026-08-03  
**Goal:** Two personally hit failure modes only — Groq TPD 429 mid-benchmark; §21 Semgrep DNS dumping raw traceback into the security panel.

**Result:** `LLMRateLimitedError` at `chat()` → hook/audit `skipped — rate limited` → callers (`agent` logic-review, `workers/common` tool loop) skip and continue. Semgrep network/DNS stderr classified to short `Semgrep scan failed: network error, falling back to logic-review` (fallback behavior unchanged). Unit tests in `tests/test_llm.py` + `tests/test_scanner_resilience.py`. Commit `575029d`.

**Independent verify (2026-08-03):**
- pytest: `test_chat_rate_limit_*`, `test_logic_review_degrades_on_rate_limit`, full `test_scanner_resilience.py` (+ hooks formatting suite) → **15 passed**
- Forced path (patch, not live burn): `ScanError(network message)` → `review_code` sets `static_scan_error` to the short string; `_display_report` shows that line in the security header; no `Traceback`/`getaddrinfo` in panel. Forced `LLMRateLimitedError` on `assess_logic_review` → summary `skipped — rate limited`, review_code completes without traceback abort.
- Happy-path live CLI `notes_idor.py` still works (Security IDOR + memory lesson-1); Architecture 0 accepted but panel body briefly leaked pre-filter claim text — tracked as §36.

**Still optional / not required for ship:** burning real Groq TPD or killing DNS to `semgrep.dev` on a cold Semgrep cache — forced injection covers the same code paths Claude asked to watch.

**Review notes:** 429 is a skippable step, not process death. Semgrep already fell back on `ScanError`; the bug was raw stderr in the CLI.

---

## 35. CLI live-trace readability (Rich stderr)

**Tool:** Cursor (build chat) · **Date:** 2026-08-03  
**Goal:** Make `[agent]` / `[tool]` mid-run stderr scannable for demos — formatting only.

**Result:** Rich color by line type (cyan agent / magenta tool), dim HH:MM:SS on live stderr; full ISO kept in file log. Padded agent/tool columns. `tests/test_hooks_formatting.py`. No change to what is logged.

**Review notes:** Deliberately **not** keyword-coloring message bodies (`llm temperature:`, `logic-review:`) — type-level color is enough; rainbow tokens hurt mid-demo skim. Dashboard mockup mining stayed closed.

---

## 36. Architecture clean-panel leaked pre-filter claim

**Tool:** Cursor (build chat) · **Date:** 2026-08-03  
**Goal:** Architecture header said clean / 0 accepted while the green panel body still showed the LLM’s dropped ownership/layering claim.

**Cause:** `run_architecture_worker` kept the LLM `summary` after filters emptied `structured_findings`; that string became `report["message"]` / panel body.

**Result:** When claims exist but every finding is filtered out, returned `summary` is always `No architecture issues found.` Agent log still records the raw claim (`claimed issues but produced none specific` + `clean — <raw>`).

**Independent verify (2026-08-03 live CLI `notes_idor.py`):** stderr still shows dropped claim; Architecture “No issues found” panel body is the generic line. Security IDOR + lesson-1 unchanged.

**Review notes:** Filters decide what’s true; LLM summary is a draft — never promote a fully-dropped draft into user-facing clean copy.

---

## 37. Real-code smoke (external OSS files) + Windows CLI encoding crash

**Tool:** Cursor (build chat) · **Date:** 2026-08-03  
**Goal:** Qualitative smoke on 3–5 real OSS Python files (not fixtures); report only, no inline fixes.

**What ran:** 5 files under `smoke_test_external/` (werkzeug security, django crypto, requests exceptions, itsdangerous encoding, tqdm utils). Full CLI review each.

**Finding A — CLI crash (fixed):** Every run exited 1 with `UnicodeEncodeError` on `≥` in `app/cli.py` gate headers (cp1252 PowerShell). Review completed and persisted; crash was display-only. **Fix (2026-08-03):** replace `≥` with ASCII `>=` in the three CLI header sites.

**Finding B — Architecture FPs:** Security clean. Architecture layering on co-located files → fixed by `__init__.py` package gate (`0f72d5b`). Isolated framework FPs (Django self-import, Werkzeug `sys.stdout`) → fixed by resolved-project-edge filter (§39).

**Review notes:** Do not fold smoke files into formal GT.

---

## 38. Architecture sibling context — require explicit package

**Tool:** Cursor / Sol · **Date:** 2026-08-03 · **Commit:** `0f72d5b`  
**Goal:** Co-located unrelated OSS files were attached as `same_package` and justified invented layering.

**Result:** Attach directory siblings only when `__init__.py` exists. Import-linked + reverse-caller context unchanged. Isolation: 0 related / 0 accepted layering on `requests_exceptions.py`. Groq Architecture suite stayed 1.0/1.0.

---

## 39. Architecture structural findings — require cited resolved project edge

**Tool:** Cursor / Sol · **Date:** 2026-08-03 · **Commit:** `87bf69d`  
**Goal:** Isolated Django/Werkzeug still accepted layering — old filter treated any non-stdlib/relative import as “enough structure,” even when evidence cited `django.*` or `sys.stdout`.

**Result:** `ImportFact` + `classify_imports()` (stdlib / resolved_project / unresolved_external). Drop `layering_violation` / `dependency_direction` unless evidence names a resolved project import edge. Django self-imports, Werkzeug `sys.stdout`+unresolved `.repr`, and stdlib claims with unrelated project imports drop; `checkout_handler` / `low_level_persistence_client` kept.

**Review notes:** Structural acceptance must be finding-specific, not existential.

---

## 40. Security coverage semantics (additive + inconclusive)

**Tool:** Cursor · **Date:** 2026-08-03 · **Pending commit**  
**Goal:** (1) Semgrep hit skipped logic-review; (2) rate-limit looked like clean; (3) truncation silent.

**Result:** `review_code` runs Semgrep + logic-review additively on reviewable files. Rate-limit/hard chat failure → `inconclusive=True`, `no_issues=False`, `logic_review_status="inconclusive"`, CLI yellow “Review incomplete”. Report metadata: `source_truncated`, `source_truncated_note`, `used_logic_review`. 151 tests (`tests/test_security_coverage.py`).

**Review notes:** Incomplete ≠ clean. Static and logic are complementary detectors, not mutually exclusive. Next: provenance-backed real-code mini-suite, then full re-measure.

---

## 41. Provenance-backed real-code Security mini-suite

**Tool:** Cursor · **Date:** 2026-08-03 · **Pending commit**
**Goal:** every prior Security number was measured against secondpass's own planted fixtures. Build a small, separate suite against real (not planted) vulnerable code with public provenance, to check the pipeline generalizes past its own fixtures at all — not to claim general reliability.

**Result:** `benchmark/real_world/` — 4 cases, each with a real CVE/GHSA advisory or documented teaching write-up, source repo, commit/tag, license, and URL (`manifest.json`): aiohttp `CVE-2024-23334` path traversal (Apache-2.0, v3.9.1→v3.9.2), GitPython `CVE-2022-24439` command injection (BSD-3-Clause, 3.1.29→3.1.30), a django-security-lab IDOR teaching case (MIT, commit `ab9e66e`, CWE-639), Label Studio `CVE-2023-43791` hardcoded `SECRET_KEY` (Apache-2.0, advisory-quoted literal). 3 of 4 have a real upstream fixed/clean counterpart; the secret case uses a representative env-var clean control (documented as such — the real post-fix `settings.py` wasn't verified verbatim). New `benchmark/ground_truth_real_world.json` (same `{file_path, finding_type}` shape) and `app/benchmark_run_real_world.py` (separate from the main suite/results, label `real_world`, never overwrites prior results). 9 unit tests (`tests/test_benchmark_real_world.py`): manifest required fields present, GT paths exist on disk, GT/manifest expected-type agreement.

Ran once, no tuning: offline (Semgrep-only) scored 0/4 recall — none of these four bug shapes (symlink traversal check bypass, unsanitized subprocess URL, missing-`owner=` ORM filter, literal `SECRET_KEY`) are catchable by default Semgrep rules, consistent with the IDOR write-up's own "standard tools miss this class" note. Live run (LLM logic-review, `LLM_PROVIDER=openai`): **4/4 true positives, 0 false positives, 0 false negatives — precision 1.0, recall 1.0.** All 4 clean/fixed counterparts stayed clean.

**Review notes:** N=4 is evidence, not proof — read as "generalizes past its own fixtures," not a calibrated accuracy claim. Semgrep alone is provably insufficient for this bug mix; the logic-review path is carrying all of the real-world recall here, which matches why the additive-detection fix in §40 mattered. Next: first full re-measure (Security own-suite, Architecture own-suite, cross-worker, this suite) and `benchmark/REPORT.md` update.

---

## 42. Stretch: human-gated closed-loop Chroma memory

**Tool:** Cursor (Grok mentoring chat + build agent) · **Date:** 2026-08-05 ~18:00 PKT · **Branch:** `feat/human-gated-chroma-memory`  
**Goal:** Close the loop Awais wanted without restoring unsupervised AI self-save. Human ACCEPT already wrote SQLite verified outcomes; Chroma stayed seed-only. Stretch = promote a concise lesson into Chroma **only after explicit human accept**, so future MemoryWorker/`search_memory` retrieval can benefit from confirmed judgment.

**What landed:**
- `record_finding_decision` (shared by CLI `decide` + `POST /outcomes`): REJECT → SQLite only; ACCEPT → SQLite always, then `_promote_accepted_lesson` → `save_finding`
- Lesson payload from structured finding only (type / truncated evidence / suggested fix / normalized path). No human reason, raw source, or prompts. Stable id `human-accept-r{review_id}-i{index}` for idempotent retries
- `save_finding` now skips when that **id already exists** (previously minted a suffix id); near-duplicate distance guard kept
- New audit stage `chroma_promote` (`saved` / `skipped` / `error`). Supervisor `chroma_save_skip` unchanged — Supervisor still never calls `save_finding`
- Chroma failure does not roll back the SQLite decision; CLI/API surface the failure
- Docs: README / ARCHITECTURE / DEMO_STUDY — Chroma = seed + human-accepted lessons; rejects remain SQLite-only
- Tests: +4 focused cases; full suite **164 passed**

**Review notes:** Gate “accepted” (≥80) ≠ human accept. This stretch keys off human ACCEPT only. Still not “AI learns by itself” — it learns from *my* confirmed decisions. Next for Week 8: rehearse/demo video; other stretches (webhook, Bandit) remain optional and droppable.

