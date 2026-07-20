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

## What's built, mapped to the assignment checklist

- Research/review agent with a web-search skill: done (Tavily)
- Memory: done (ChromaDB, seeded from 5 real security lessons, semantically verified to discriminate between bug types)
- Hook logging every tool call with timestamps: done (app/hooks.py)
- File-read plugin: done (the scanner + agent loop both read and reason over real source files)
- Multi-hop demo: done (scan finds nothing on the IDOR file, falls back to logic review, checks memory, checks web, produces a synthesized fix, all chained in one review)

Next up (Week 5): exposing review_code() as an MCP tool so it can be called directly from Cursor, and splitting the internal logic (memory lookup vs web lookup) into a supervisor + worker multi-agent structure instead of one flat loop. Same core project, not a new one.
