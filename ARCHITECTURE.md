# secondpass — architecture (slide / panel)

Use this file for slides and deeper explanation. The README keeps the one-screen version.

---

## 1. System at a glance

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINTS (triggers)                          │
│                                                                          │
│    CLI (`app.cli`)   ·   FastAPI (`app.api`)   ·   MCP   ·   Dashboard   │
│    review / decide       submit → poll → get         review_code         │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         SUPERVISOR (`app/supervisor.py`)                 │
│                                                                          │
│   1. Run Security worker on target                                       │
│   2. Run Architecture worker on target                                   │
│   3. Aggregate accepted / needs_review + short summary                   │
│   4. Persist review (+ audit events when job_id present)                 │
└───────────────────────┬─────────────────────────┬────────────────────────┘
                        │                         │
            ┌───────────▼───────────┐   ┌─────────▼──────────┐
            │   SECURITY WORKER     │   │ ARCHITECTURE WORKER│
            │   (security path)     │   │ (architecture path)│
            └───────────┬───────────┘   └─────────┬──────────┘
                        │                         │
                        └───────────┬─────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         SHARED CONTRACT                                  │
│                                                                          │
│   Finding schema (type, evidence, confidence, fix, detection method)     │
│   Confidence gate  →  accepted  |  needs_review                          │
│   Deterministic post-filters (bleed / attribution / structure)           │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         STATE                                            │
│                                                                          │
│   SQLite `.secondpass/`     reviews · verified_outcomes · audit_events   │
│   ChromaDB `.chromadb/`     curated seed lessons (retrieval during review)│
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Security worker (what happens on one file)

```text
Target path
    │
    ▼
 Semgrep static scan  ──────────────────┐
    │                                   │ (empty / soft)
    ▼                                   ▼
 Normalized scan findings      LLM logic / authorization review
    │                                   │
    └───────────────┬───────────────────┘
                    ▼
         Map → Finding schema
                    │
                    ▼
     Optional: memory hit + web context
                    │
                    ▼
         Confidence gate (≥ threshold)
                    │
                    ▼
     Post-filter: architecture category bleed
                    │
                    ▼
         accepted  /  needs_review
```

**Why both Semgrep and logic review:** Semgrep catches many rule-shaped issues; ownership / IDOR-style gaps often need the LLM pass. Memory retrieval is your *curated seed* lessons in Chroma — not a generic AppSec corpus, and not the SQLite verified-outcome log.

---

## 3. Architecture worker (what happens on one file)

```text
Target path
    │
    ▼
 Cross-file context (`app/context.py`)
   · AST imports → same-package siblings
   · reverse callers (first-party, capped)
    │
    ▼
 LLM architecture review (naming / layering / dependency / duplication)
    │
    ▼
 Map → Finding schema
    │
    ▼
 Confidence gate
    │
    ▼
 Post-filters (in order of intent):
   1. security / authz category bleed   → drop (belongs to Security)
   2. sibling-file misattribution       → drop (evidence about context file)
   3. insufficient structure            → drop layering/dependency when
                                          target has no first-party imports
    │
    ▼
 accepted  /  needs_review
```

**Panel line:** cross-file context is required for real architecture bugs; the same feature caused sibling double-counting until the attribution filter — measured in the benchmark report.

---

## 4. Memory model (two stores on purpose)

| Store | What | Who writes |
| --- | --- | --- |
| **Chroma** | Semantic seed lessons (`security_lessons.json`) **plus** human-confirmed accepted lessons — retrieved during Security review | Seeded at setup; **human ACCEPT** via CLI/dashboard (Supervisor **does not** auto-`save_finding`) |
| **SQLite verified outcomes** | Accept / reject + reason on a concrete finding | You, via CLI or dashboard |

Supervisor does **not** auto-promote findings into Chroma. A human ACCEPT writes SQLite first, then may promote a concise lesson into Chroma for future `search_memory` retrieval. Rejects remain SQLite-only. Gate “accepted” (confidence ≥ threshold) is not the same as human accepted.

---

## 5. Dashboard / API path

```text
Browser (Vite) ──HTTP──▶ FastAPI
                           │
                           ├─ POST /reviews     → enqueue job
                           ├─ GET  /reviews/jobs/{id}  → poll status
                           ├─ GET  reviews / findings / outcomes
                           └─ worker thread runs Supervisor (same as CLI)
```

No separate “dashboard backend.” CORS only; same Supervisor, same DB.

---

## 6. What to say on the slide (60–90 seconds)

1. **Triggers** — CLI for daily use; API + UI for history/demo; MCP when the IDE should call it.
2. **Supervisor** — one orchestration point; Security then Architecture; one aggregated report.
3. **Same schema + gate on both workers** — comparable outputs, not two ad-hoc formats.
4. **Hard filters after the LLM** — where precision actually moved (bleed, attribution, invented structure).
5. **Two memory stores** — Chroma retrieves seed + human-accepted lessons; SQLite records every human decision (rejects never enter Chroma).
6. **Measured** — point to `benchmark/REPORT.md` (Security 1.0/1.0; Architecture Groq 1.0/1.0; OpenAI label split called out honestly).

Skip stretches (webhook, Bandit, auto-memory) unless asked.
