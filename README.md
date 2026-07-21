# secondpass

A **personal security review agent** for the command line.

It runs a static scan, checks findings against _your_ past security lessons (persistent memory), optionally pulls public guidance from the web, then writes a short explanation and fix suggestion. Built as a “second pass” over your own recurring mistakes — not a replacement for Semgrep, CodeRabbit, or a full AppSec program.

---

## Architecture

High-level component map:

```text
                         ┌──────────────────────────────────────┐
                         │              CLI (cli.py)            │
                         │     review <path>  |  review --diff  │
                         └──────────────────┬───────────────────┘
                                            │
                              ┌─────────────▼──────────────┐
                              │     Agent loop (agent.py)  │
                              │  plan → tool calls → report│
                              └─────────────┬──────────────┘
                                            │
                     always first           │ tool calls
                            ┌───────────────┼───────────────┐
                            ▼               ▼               ▼
                   ┌────────────────┐ ┌───────────┐ ┌──────────────┐
                   │ Scanner        │ │ LLM       │ │ Skills       │
                   │ scanner.py     │ │ llm.py    │ │              │
                   │ Semgrep (+     │ │ Groq /    │ │ Memory       │
                   │ logic fallback)│ │ Gemini /  │ │  memory.py   │
                   └────────┬───────┘ │ OpenRouter│ │  ChromaDB    │
                            │         └─────┬─────┘ │              │
                            │               │       │ Web search   │
                            │               ├───────►  websearch.py│
                            │               │       │  Tavily      │
                            │               │       │              │
                            │               └───────► save_finding │
                            │                       └──────┬───────┘
                            │                              │
                            │         ┌────────────────────┘
                            │         │
                            ▼         ▼
                   ┌──────────────────────────────────────┐
                   │     Rich report (scan · memory ·     │
                   │         web · explanation / fix)     │
                   └──────────────────────────────────────┘

  Optional (--diff only):
    gitdiff.py ──► changed files + line ranges ──► Agent
                   (filter findings to touched lines)

  Cross-cutting:
    hooks.py wraps Scanner / Memory / Web search
    (logs tool name, args, duration)
```

| Component      | Job                                                                          |
| -------------- | ---------------------------------------------------------------------------- |
| **CLI**        | Entry point; path review or `--diff` mode; prints the Rich report            |
| **Agent**      | Planner loop — runs the scanner, lets the LLM call skills, builds the report |
| **LLM**        | Provider-agnostic `chat()` with tool calling                                 |
| **Scanner**    | Semgrep → normalized findings (logic fallback if scan is empty)              |
| **Memory**     | Semantic search over past lessons; optional `save_finding`                   |
| **Web search** | Tavily → title / url / snippet for public guidance                           |
| **Git diff**   | Picks staged/unstaged files + changed line ranges for `--diff`               |
| **Hooks**      | Logs timestamp, tool name, args, and duration for every skill call           |

---

## Features

- **Static scan** via Semgrep (`p/python`, `p/javascript`)
- **Git diff mode** — review only what you changed (staged preferred)
- **Persistent memory** with ChromaDB, seeded from `security_lessons.json`
- **Web search** via Tavily for OWASP / remediation context
- **Provider-agnostic LLM** — Groq, Gemini, or OpenRouter (OpenAI-compatible APIs)
- **Tool-calling agent loop** with logged tool calls (`[tool] …` in the console)
- **Near-duplicate guard** — won’t spam memory with lessons that already match closely
- **Graceful degradation** — if Semgrep, memory, or web search fail, review continues with what it has

---

## Requirements

- Python 3.10+
- Git (for `--diff`)
- Semgrep (installed via `requirements.txt`)
- API keys:
    - One LLM provider: **Groq**, **Gemini**, or **OpenRouter**
    - **Tavily** (for web search)

---

## Setup

```bash
git clone <your-repo-url> secondpass
cd secondpass

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

> **Windows tip:** If `python` opens the Microsoft Store, use `py -3.12 -m venv .venv` or disable the `python.exe` App Execution Alias.

Edit `.env`:

```env
LLM_PROVIDER=groq          # groq | gemini | openrouter
GROQ_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
LLM_MODEL=                 # optional override
TAVILY_API_KEY=...
```

Only the key for your chosen `LLM_PROVIDER` is required (plus Tavily if you want web context).

---

## Usage

Run commands as a module from the project root:

```bash
python -m app.cli --help
```

### Review a specific path

```bash
python -m app.cli review path/to/file_or_dir
```

### Review your git changes (`--diff`)

```bash
python -m app.cli review --diff
```

**How `--diff` chooses changes**

1. Prefer **`git diff --staged`** — what you’re about to commit (pre-commit style).
2. If nothing is staged, fall back to **unstaged** `git diff` so the command is still useful mid-edit.

**How findings are scoped**

- Changed **files** are scanned as **whole files** (Semgrep/logic review need surrounding context).
- The report **only keeps findings whose line falls inside the diff hunks** on the new side of the patch.
- File-level logic-review fallbacks are kept when the file was touched at all.

Do not combine modes: use either `review <path>` **or** `review --diff`.

What you’ll see:

1. Live **tool-call logs** (`run_static_scan`, `search_memory`, `search_web`, …)
2. A **structured Rich report** per finding:
    - Scan detail
    - Matched memory lesson (+ confidence)
    - Web context (if used)
    - LLM explanation + suggested fix

If Semgrep finds nothing but the file has code, secondpass falls back to a **logic/authorization review** (useful for IDOR / missing ownership checks that static rules often miss).

### Memory only

```bash
python -m app.cli search-memory "user can read someone else's data"
```

### Web search only

```bash
python -m app.cli search-web "OWASP broken access control A01"
```

---

## How it works

| Piece                   | Role                                                     |
| ----------------------- | -------------------------------------------------------- |
| `app/scanner.py`        | Runs Semgrep, normalizes findings                        |
| `app/gitdiff.py`        | Collects staged/unstaged diffs + changed line ranges     |
| `app/memory.py`         | ChromaDB lesson store + semantic search + `save_finding` |
| `app/websearch.py`      | Tavily → `{title, url, snippet}`                         |
| `app/llm.py`            | Single `chat(messages, tools=…)` across providers        |
| `app/hooks.py`          | Logs every tool call (console + `tool_calls.log`)        |
| `app/agent.py`          | Planner loop: scan → LLM tool calls → structured report  |
| `app/cli.py`            | Typer CLI + Rich output                                  |
| `security_lessons.json` | Seed lessons (your real past bugs)                       |

**Agent flow (simplified):**

1. Always run Semgrep first (whole file, even in `--diff` mode)
2. For each finding, let the LLM decide whether to search memory, search the web, and/or save a _new_ lesson
3. Cap tool rounds (default 6) to avoid infinite loops
4. In `--diff` mode, drop findings outside changed line ranges
5. Print explanation + fix

`save_finding` only persists when the issue looks meaningfully new. Close matches to existing lessons are skipped (prompt + distance guard).

---

## Personalizing memory

Edit `security_lessons.json`, then delete `.chromadb/` so the next run re-seeds:

```json
{
    "id": "lesson-6",
    "type": "Broken Access Control",
    "pattern": "short description of the bug pattern",
    "bad_example": "minimal bad snippet",
    "fix": "what to do instead",
    "source": "where you learned it"
}
```

Lessons confirmed during reviews can also be added at runtime via `save_finding` (when they’re not near-duplicates).

---

## Project layout

```text
secondpass/
├── app/
│   ├── agent.py
│   ├── cli.py
│   ├── gitdiff.py
│   ├── hooks.py
│   ├── llm.py
│   ├── memory.py
│   ├── scanner.py
│   └── websearch.py
├── security_lessons.json
├── requirements.txt
├── .env.example
├── README.md
└── prompts.md            # build log / assignment notes
```

---

## Notes & limits

- This is a **personal** assistant around _your_ lessons + Semgrep, not a complete SAST platform.
- Logic bugs (e.g. missing ownership checks) may not appear in Semgrep; the logic-review fallback exists for that.
- `--diff` ignores deleted/binary files and only reviews paths that still exist in the working tree.
- Groq tool-calling can occasionally fail formatting; the agent retries and continues when possible.
- `.env`, `.chromadb/`, and `tool_calls.log` are gitignored — keep keys out of the repo.

---

## License

Use and adapt freely for personal or team workflows. Add a license file if you publish this publicly.
