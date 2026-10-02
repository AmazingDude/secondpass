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
- Semgrep (installed from the package's dependencies)
- API keys for assisted reviews: one of **groq** / **openai** / **gemini** / **openrouter**; **Tavily** optional

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

pip install -e ".[memory]"
cp .env.example .env
```

Lesson memory is optional. `pip install -e .` installs the base app without
ChromaDB; use it for static scans or assisted reviews with `--no-memory`.
The assisted default still requests memory: install `.[memory]` as shown above,
or disable retrieval explicitly. Requested memory that cannot run still makes
the review incomplete, and `search-memory` reports how to install ChromaDB.
Existing lesson data is not removed when installing without the extra.

For compatibility, `pip install -r requirements.txt` installs the app with
memory and the console script. Run it from the repository root; dependencies
are declared in `pyproject.toml`, not duplicated in the requirements file.

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

Writable state depends on how SecondPass is run. A Git checkout keeps its existing
`.secondpass/secondpass.db`, `.chromadb/`, and `tool_calls.log`. An installed wheel
uses the operating system's per-user data and log directories instead of writing
beside its installed code. To choose an absolute writable directory for all three
stores, set `SECONDPASS_DATA_DIR` in the process environment before starting the
CLI, API, or MCP server. This does not migrate data between locations; existing
checkout history remains in place.

Run `secondpass --help` or `secondpass doctor` without credentials or network
access. Neither command loads `.env` or initializes lesson memory. `doctor`
lists local package versions and state paths; runtime startup, credentials and
state writability require separate checks. It exits with status 1 for missing
required packages or an invalid data-directory override and prints repair steps.
Absent ChromaDB is labeled optional and does not fail this base inventory;
that does not mean an explicitly requested lesson-memory operation will work.

Primary Architecture eval numbers use **Groq** at temperature 0. OpenAI can disagree on neighboring Architecture labels for the same bug. See [`benchmark/REPORT.md`](benchmark/REPORT.md) §4.

---

## CLI

### Try a scan without model keys

After installing the Python package and its dependencies, run:

```bash
secondpass review path/to/file.py --mode static
```

This file-only mode uses a bundled first-party Semgrep rule for
`subprocess.run(..., shell=True)`. It does not load `.env`, call a model, retrieve
lessons or search the web; no provider key is needed. Matches are risky API
patterns to inspect, not confirmed vulnerabilities. Zero matches does not imply
the absence of security or architecture bugs. An incomplete scan exits with
status 1 and retains any available matches; a complete scan exits with status 0,
even when matches exist. Static scans are not saved to review history yet.

The bundled rule does not require registry downloads; metrics and version checks
are disabled. Semgrep must still run on your machine. The real-engine smoke test
runs on Linux CI; Windows engine startup has not been validated. Directory and
diff reviews, the API, dashboard and MCP remain assisted workflows. Omitting
`--mode` preserves the existing assisted default and its provider requirements.

### Assisted reviews and history

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

Lesson retrieval is enabled by default. Add `--no-memory` to file, directory or
diff reviews to skip lesson-store initialization and retrieval, including its
embedding startup/downloads. For example, run `secondpass review path/to/file.py --no-memory`.
This is not an offline mode: scanners, model review and optional
web research still use their existing configuration. Disabling retrieval is not
a coverage failure; requesting it when the store cannot initialize still makes
the review inconclusive. Explicit searches and human decision/promotion commands
are separate actions and are not disabled by this per-review option.

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

Saved run lookup (local API): `GET /v1/runs` groups SQLite review records by
their recorded `job_id`; `GET /v1/runs/{job_id}` returns the group's worker
reviews with their original numeric review IDs. Both work without a live job
in memory. These are **legacy result projections**, not durable execution:
run lifecycle and overall coverage are unknown, even when a recorded worker
has `coverage_status=ok`. Missing request options, origin, snapshots and run
start/end times are not reconstructed. Worker findings, incomplete/unverified
signals and linked human decisions remain available through existing routes.

Both endpoints return `schema_version=1`, accept `limit` (1–100, default 50),
and return `snapshot_review_id` plus `next_before_review_id`. For the next page,
send the same snapshot as `snapshot_review_id` and the returned next ID as
`before_review_id`; stop when the next ID is null. Run groups are ordered by
their newest saved review ID, and detail records by review ID, both descending.
The snapshot freezes appended review records for that traversal, not source
content or execution state. Refresh without a snapshot to see newer records.
Records without a nonempty job ID remain individual reviews; audit-only jobs
have no result group. Exact lookup returns 404 when no linked reviews exist.
The current `/reviews/jobs/{job_id}` endpoint still describes live in-memory
jobs and does not recover lifecycle after restart. No dashboard run browser,
job resumption or hosted authentication is included; keep the API local.

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
├── app/                      # CLI, workers, API, memory, bundled security_lessons.json
├── web/                      # Vite + React dashboard
├── benchmark/
│   ├── fixtures/             # planted suite
│   ├── real_world/           # provenance CVE/teaching mini-suite
│   ├── ground_truth*.json
│   └── REPORT.md
├── tests/
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
