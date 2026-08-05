# secondpass

A **personal security + architecture review agent**.

It runs Semgrep and an LLM logic/authorization pass, then an architecture pass, under one Supervisor. Findings share a schema and confidence gate. Security can retrieve curated personal lessons from Chroma; you record accept/reject decisions in SQLite. Optional Tavily web context. Built as a second pass over your own recurring mistakes — not a replacement for a full AppSec program.

Detection-quality journey, final numbers, and known limits: [`benchmark/REPORT.md`](benchmark/REPORT.md). Slide-ready system map: [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## What it does

| Surface | Role |
| --- | --- |
| **CLI** | Review a path or git diff; decide accept/reject; list reviews / outcomes / audit |
| **Supervisor** | Security → Architecture → combined summary |
| **Security** | Semgrep **and** LLM logic/authorization (additive) → schema → confidence gate |
| **Architecture** | Layering / dependency (and soft naming) with cross-file context + post-filters |
| **Memory** | Chroma: seed lessons **plus** human-confirmed accepted lessons (retrieval); SQLite: every accept/reject verified outcome (audit trail). Rejects stay SQLite-only. |
| **API** | FastAPI async jobs: submit → poll → results |
| **Dashboard** | Vite + React: Submit, Findings, History, Memory |
| **MCP** | Stdio `review_code` for Cursor / Claude Code / other clients |

**Coverage honesty:** if logic-review cannot complete (e.g. LLM rate limit), the review is **inconclusive** — not “clean,” and not the same as a low-confidence **needs review** finding. The CLI and dashboard treat those states separately.

---

## Architecture

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

**30-second talk track:** one Supervisor, two workers, same schema + gate; Chroma retrieves seed lessons and human-accepted promotions; SQLite stores every decision (rejects never enter Chroma). Hard post-filters after the LLM (category bleed, target attribution, insufficient structure, package/import-edge rules) are why Architecture precision moved — see the report.

---

## Requirements

- Python 3.10+
- Node.js 20+ (dashboard only)
- Git (for `--diff`)
- Semgrep (via `requirements.txt`)
- API keys: one of **groq** / **openai** / **gemini** / **openrouter**; **Tavily** optional

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

```env
LLM_PROVIDER=groq          # groq | openai | gemini | openrouter
GROQ_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
LLM_MODEL=                 # optional; leave empty for provider default
TAVILY_API_KEY=...
```

Only the key for your chosen `LLM_PROVIDER` is required. If `LLM_MODEL` is set to an OpenAI id while using Groq, Groq will 404 — clear it or set a model that provider accepts.

Primary Architecture eval numbers use **Groq** at temperature 0. OpenAI can disagree on neighboring Architecture labels for the same bug — see [`benchmark/REPORT.md`](benchmark/REPORT.md) §4.

---

## CLI

```bash
python -m app.cli --help

python -m app.cli review path/to/file_or_dir
python -m app.cli review --diff

python -m app.cli decide <review_id> --decision accept --reason "real IDOR"
python -m app.cli list-reviews
python -m app.cli list-outcomes
python -m app.cli audit <job_id>

python -m app.cli search-memory "user can read someone else's data"
python -m app.cli search-web "OWASP broken access control A01"
```

Use either `review <path>` **or** `review --diff`, not both.

---

## API + dashboard

```bash
# Terminal 1
python -m app.api

# Terminal 2
cd web && npm install && npm run dev
```

API default: `http://127.0.0.1:8000`. UI usually `http://127.0.0.1:5173` (`VITE_API_BASE` to override).

Views: **Submit** → **Findings** → **History** → **Memory** (verified outcomes). History shows **Incomplete** when coverage failed — not **Clean**.

---

## Benchmarks

```bash
python -m app.benchmark_run --label my_run
python -m app.benchmark_run_architecture --label my_arch_run
python -m app.benchmark_cross_worker --label my_cross_run
python -m app.benchmark_run_real_world --label my_real_world_run
```

Results JSON under `benchmark/results/` (gitignored). Written report: [`benchmark/REPORT.md`](benchmark/REPORT.md).

**Final snapshot (2026-08-04), accepted findings only:**

| Suite | Provider | Precision | Recall | Notes |
| --- | --- | ---: | ---: | --- |
| Security own-suite | Groq | 1.0 | 1.0 | 4 planted classes + clean / reverse-bleed rows |
| Architecture own-suite | Groq | 1.0 | 1.0 | layering + dependency-direction fixtures |
| Cross-worker | Groq | — | status **ok** | 0 findings both directions |
| Real-world Security mini-suite | OpenAI | 1.0 | 1.0 | **N=4** provenance CVE/teaching cases — generalization evidence, **not** calibrated accuracy |

Semgrep-only on that real-world set scored **0/4** recall; logic-review carries those detections. Suites are intentionally narrow and single-file scoped — see the report’s known limitations (including four Architecture FP modes found and closed in sequence).

---

## MCP

```bash
python -m app.mcp_server
```

Exposes `review_code` (`path` and/or `diff=true`). Point the client at this repo’s venv Python with `cwd` = project root. Keys load from `.env`.

```bash
python -m app.mcp_client_smoke path/to/file.py
```

---

## Project layout

```text
secondpass/
├── app/                      # CLI, Supervisor, workers, API, LLM, scanner, memory
├── web/                      # Vite + React dashboard
├── benchmark/
│   ├── fixtures/             # planted suite
│   ├── real_world/           # provenance CVE/teaching mini-suite
│   ├── ground_truth*.json
│   └── REPORT.md
├── tests/
├── security_lessons.json
├── requirements.txt
├── .env.example
├── README.md
├── ARCHITECTURE.md
├── DEMO.md                   # Week 8 panel walkthrough (run-of-show)
├── DEMO_STUDY.md             # deep understanding / Q&A prep
└── prompts.md                # build / learning log
```

---

## Notes & limits

- Personal tool — not a complete SAST platform. Do not claim reliability on arbitrary real-world repos from these numbers alone.
- Verified outcomes are a human decision log; they do not yet change what the next review retrieves.
- Confidence is LLM self-reported; temperature=0 cuts variance, it does **not** calibrate confidence.
- Architecture label stability can be provider-dependent (`layering_violation` vs `dependency_direction`).
- Incomplete coverage (`inconclusive`) ≠ clean ≠ needs_review.
- `.env`, `.chromadb/`, `.secondpass/`, and `benchmark/results/*` stay local / gitignored.

---

## License

[MIT](LICENSE) — use, modify, and share freely.
