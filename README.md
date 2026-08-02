# secondpass

A **personal security + architecture review agent**.

It runs a static scan, a logic/authorization pass, and an architecture pass under one Supervisor, gates findings by confidence, retrieves curated personal lessons from Chroma during Security review, records human accept/reject decisions in SQLite, and optionally pulls public remediation context. Built as a second pass over your own recurring mistakes — not a replacement for a full AppSec program.

**Memory honesty:** Chroma holds ~5 curated seed lessons and *does* retrieve during reviews. SQLite verified outcomes are an explicit human decision log (Awais’s required write path) — they are **not** fed back into retrieval yet. Closing that loop is a named stretch (automatic memory updates), not current behavior.

Detection-quality journey (baselines, hardens, final numbers, known limits): [`benchmark/REPORT.md`](benchmark/REPORT.md).

---

## What it does

| Surface | Role |
| --- | --- |
| **CLI** | Review a path or git diff; decide accept/reject; list reviews / outcomes / audit |
| **Supervisor** | Security worker → Architecture worker → combined summary |
| **Security** | Semgrep + LLM logic/authorization review → schema → confidence gate |
| **Architecture** | Naming / layering / dependency smells with cross-file context (first-party siblings) |
| **Memory** | Chroma: curated seed lesson *retrieval*; SQLite: verified outcomes (accept/reject + reason) — write path only until stretch closed-loop |
| **API** | FastAPI async jobs: submit → poll → results |
| **Dashboard** | Vite + React: Submit, Findings, History, Memory |
| **MCP** | Stdio server exposing `review_code` for Cursor / Claude Code / other clients |

---

## Architecture

Panel walkthrough (one screen). A fuller slide-ready map lives in [`ARCHITECTURE.md`](ARCHITECTURE.md).

```text
Triggers: CLI · API · MCP · Dashboard
                │
                ▼
          Supervisor
     ┌──────────┴──────────┐
     ▼                     ▼
 Security              Architecture
 Semgrep + logic       cross-file context
 schema → gate         schema → gate
     │                     │
     └──────────┬──────────┘
                ▼
   SQLite (reviews, audit, verified outcomes)
   + Chroma lessons (retrieval)
```

**Talk track (30 seconds):** one Supervisor, two workers, same schema + confidence gate on both sides; Chroma retrieves seed lessons; SQLite stores your accept/reject decisions (not yet re-injected into the next review). Hard filters after the LLM (category bleed, sibling attribution, insufficient structure) are why the benchmark numbers moved — details in [`benchmark/REPORT.md`](benchmark/REPORT.md).

---

## Requirements

- Python 3.10+
- Node.js 20+ (dashboard only)
- Git (for `--diff`)
- Semgrep (via `requirements.txt`)
- API keys: one LLM provider (**groq**, **openai**, **gemini**, or **openrouter**); **Tavily** optional for web context

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

Edit `.env`:

```env
LLM_PROVIDER=groq          # groq | openai | gemini | openrouter
GROQ_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
LLM_MODEL=                 # optional override (leave empty for provider default)
TAVILY_API_KEY=...
```

Only the key for your chosen `LLM_PROVIDER` is required. If you set `LLM_MODEL` to an OpenAI id while using Groq, Groq will 404 — clear `LLM_MODEL` or set a model that provider accepts.

Primary Architecture eval numbers in the report used **Groq** at temperature 0. OpenAI can disagree on neighboring finding-type labels for the same bug — see [`benchmark/REPORT.md`](benchmark/REPORT.md) §4.

---

## CLI

```bash
python -m app.cli --help

# Full review (Security + Architecture under Supervisor)
python -m app.cli review path/to/file_or_dir

# Git changes only (Security-scoped to diff hunks; prefer staged)
python -m app.cli review --diff

# Verified outcomes
python -m app.cli decide <review_id> --decision accept --reason "real IDOR"
python -m app.cli list-reviews
python -m app.cli list-outcomes
python -m app.cli audit <job_id>

# Utilities
python -m app.cli search-memory "user can read someone else's data"
python -m app.cli search-web "OWASP broken access control A01"
```

Do not combine modes: use either `review <path>` **or** `review --diff`.

---

## API + dashboard

Terminal 1 — API (default `http://127.0.0.1:8000`):

```bash
python -m app.api
```

Terminal 2 — UI:

```bash
cd web
npm install
npm run dev
```

Open the Vite URL (usually `http://127.0.0.1:5173`). Override API base with `VITE_API_BASE` if needed.

Dashboard views: **Submit** a path → **Findings** → **History** (prior reviews) → **Memory** (record / browse verified outcomes).

---

## Benchmarks

Planted fixtures + ground truth live under `benchmark/`. Evaluators:

```bash
python -m app.benchmark_run --label my_run
python -m app.benchmark_run_architecture --label my_arch_run
python -m app.benchmark_cross_worker --label my_cross_run
```

Results write under `benchmark/results/` (gitignored JSON). The written reliability report is committed: [`benchmark/REPORT.md`](benchmark/REPORT.md).

Final snapshot (2026-08-02), accepted findings only:

| Suite | Precision | Recall | Notes |
| --- | ---: | ---: | --- |
| Security own-suite | 1.0 | 1.0 | 4 planted bug classes + clean / reverse-bleed rows |
| Architecture (Groq A/B) | 1.0 | 1.0 | Matches post-attribution / reverse-bleed record |
| Architecture (OpenAI final) | 0.5 | 0.5 | Same bug found; wrong neighboring `finding_type` on one fixture |

Suite is intentionally narrow — see the report’s known limitations.

---

## MCP

```bash
python -m app.mcp_server
```

Exposes `review_code` over stdio (`path` and/or `diff=true`). Point your client’s command at this repo’s venv Python, with `cwd` set to the project root. Keys still load from `.env`.

Smoke without an external client:

```bash
python -m app.mcp_client_smoke path/to/file.py
```

---

## Project layout

```text
secondpass/
├── app/                 # CLI, Supervisor, workers, API, LLM, scanner, memory, …
├── web/                 # Vite + React dashboard
├── benchmark/           # fixtures, ground truth, REPORT.md
├── tests/
├── security_lessons.json
├── requirements.txt
├── .env.example
├── README.md
├── ARCHITECTURE.md      # slide / panel system map
└── prompts.md           # build / learning log (assignment process)
```

---

## Notes & limits

- Personal tool around curated seed lessons + scan/logic/architecture passes — not a complete SAST platform. Verified outcomes do not yet change what the next review retrieves.
- Confidence is LLM self-reported; temperature=0 cuts variance, it does **not** calibrate confidence.
- Provider choice affects Architecture label stability on overlapping types (`layering_violation` vs `dependency_direction`).
- `--diff` ignores deleted/binary files and only reviews paths that still exist.
- `.env`, `.chromadb/`, `.secondpass/`, and `benchmark/results/*.json` stay local / gitignored — keep keys out of the repo.

---

## License

[MIT](LICENSE) — use, modify, and share freely.
