# secondpass

A **personal security + architecture review agent**.

It runs Semgrep and an LLM logic/authorization pass, then an architecture pass, under one Supervisor. Findings share a schema and confidence gate. Security can retrieve curated personal lessons from Chroma; you record accept/reject decisions in SQLite. Optional Tavily web context. Built as a second pass over your own recurring mistakes, not a replacement for a full AppSec program.

Detection-quality journey, final numbers, and known limits: [`benchmark/REPORT.md`](benchmark/REPORT.md). System map: [`ARCHITECTURE.md`](ARCHITECTURE.md).

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

**Coverage honesty:** if logic-review cannot complete (e.g. LLM rate limit), the review is **inconclusive**, not “clean,” and not the same as a low-confidence **needs review** finding. The CLI and dashboard treat those states separately.

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

One Supervisor, two workers, same schema + gate. Chroma retrieves seed lessons and human-accepted promotions; SQLite stores every decision (rejects never enter Chroma). Hard post-filters after the LLM (category bleed, target attribution, insufficient structure, package/import-edge rules) are why Architecture precision moved. See the report.

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

pip install -e .
cp .env.example .env
```

(`pip install -r requirements.txt` still works if you only need the deps without the `secondpass` console script.)

```env
LLM_PROVIDER=groq          # groq | openai | gemini | openrouter
GROQ_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
LLM_MODEL=                 # optional; leave empty for provider default
TAVILY_API_KEY=...
```

Only the key for your chosen `LLM_PROVIDER` is required. If `LLM_MODEL` is set to an OpenAI id while using Groq, Groq will 404: clear it or set a model that provider accepts.

Primary Architecture eval numbers use **Groq** at temperature 0. OpenAI can disagree on neighboring Architecture labels for the same bug. See [`benchmark/REPORT.md`](benchmark/REPORT.md) §4.

---

## CLI

```bash
secondpass --help
# alternative without editable install: python -m app.cli --help

secondpass review path/to/file_or_dir
secondpass review --diff

# Directory reviews (bounded; same semantics as the dashboard)
secondpass review path/to/dir --max-files 10 --workers 2

secondpass decide --review-id <id> --index 0 --accept --reason "real IDOR"
secondpass list-reviews
secondpass list-outcomes
secondpass audit <job_id>

secondpass search-memory "user can read someone else's data"
secondpass search-web "OWASP broken access control A01"
```

Use either `review <path>` **or** `review --diff`, not both. For directories, `--workers` is concurrent **file** reviews; Security and Architecture still both run per file.

---

## API + dashboard

```bash
# Terminal 1
python -m app.api

# Terminal 2
cd web && npm install && npm run dev
```

API default: `http://127.0.0.1:8000`. Interactive OpenAPI docs: [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs) (also `/redoc`). UI usually `http://127.0.0.1:5173` (`VITE_API_BASE` to override).

Views: **Submit**, **Findings**, **History**, **Memory**. After a job completes, Submit keeps the live timeline/audit trail; open Findings when ready. History shows **Incomplete** when coverage failed, not **Clean**. Directory submit supports `workers` / `max-files` / include-exclude (same as CLI).

---

## Docs map

| Doc | What it’s for |
| --- | --- |
| This README | Setup, CLI/API/MCP surfaces, benchmark snapshot |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System map and wiring |
| [`Phase3_PRD.md`](Phase3_PRD.md) | Mentor-approved Phase 3 scope |
| [`benchmark/REPORT.md`](benchmark/REPORT.md) | Detection-quality journey and scored results |
| [`prompts.md`](prompts.md) | Chronological build / interaction log |
| [`REFLECTION.md`](REFLECTION.md) | What AI did well, where it failed, lessons learned |

---

## Benchmarks

```bash
python -m app.benchmark_run --label my_run
python -m app.benchmark_run_architecture --label my_arch_run
python -m app.benchmark_cross_worker --label my_cross_run
python -m app.benchmark_run_real_world --label my_real_world_run
```

Results JSON under `benchmark/results/` (gitignored). Written report: [`benchmark/REPORT.md`](benchmark/REPORT.md).

**Measured snapshot (accepted findings only; see REPORT for full A/B notes):**

| Suite | Provider | Precision | Recall | Notes |
| --- | --- | ---: | ---: | --- |
| Security own-suite | Groq | 1.0 | 1.0 | 4 planted classes + clean / reverse-bleed rows (2026-08-04; clean reconfirm 2026-08-09) |
| Security own-suite | OpenAI | 1.0 | 1.0 | Same planted suite (confidence-bucket run 2026-08-08) |
| Architecture own-suite | Groq | 1.0 | 1.0 | layering + dependency-direction fixtures |
| Architecture own-suite | OpenAI | 1.0 | 0.5 | Same fixtures; one evidence-bar / claim_unverified miss (provider gap) |
| Cross-worker | Groq | n/a | status **ok** | 0 findings both directions |
| Real-world Security mini-suite | OpenAI | 1.0 | 1.0 | **N=4** provenance CVE/teaching cases |
| Real-world Security mini-suite | Groq | 1.0 | 1.0 | Same **N=4** suite, independent A/B |

Semgrep-only on that real-world set scored **0/4** recall; logic-review carries those detections. Suites are intentionally narrow and single-file scoped. Provider-labeled rows only: do not mix OpenAI and Groq into one unlabeled score. Full confidence-bucket table and inconclusive-scoring notes: REPORT §5.

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
├── pyproject.toml            # local `pip install -e .` → `secondpass` CLI
├── .env.example
├── ARCHITECTURE.md
├── Phase3_PRD.md
├── REFLECTION.md             # Week 8: AI wins / fails / lessons
├── prompts.md                # chronological build log
└── README.md
```

---

## Notes & limits

- Personal tool, not a complete SAST platform. Do not claim reliability on arbitrary real-world repos from these numbers alone.
- Logic-review runs additively with Semgrep (not only when the static scan is empty). Inconclusive coverage ≠ clean ≠ needs_review.
- Verified outcomes are always written to SQLite. Human ACCEPT may also promote a concise lesson into Chroma for later retrieval; REJECT stays SQLite-only. Near-duplicates of existing lessons are skipped.
- Confidence is LLM self-reported; temperature=0 cuts variance, it does **not** calibrate confidence.
- Architecture label stability can be provider-dependent (`layering_violation` vs `dependency_direction` / claim_unverified).
- Local venv (or a local container) is enough to run the stack; public hosting is optional.
- `.env`, `.chromadb/`, `.secondpass/`, and `benchmark/results/*` stay local / gitignored.

---

## License

[MIT](LICENSE). Use, modify, and share freely.
