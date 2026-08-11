# secondpass: Reflection (Week 8)

**Required deliverable:** what AI did well, where it failed, and lessons learned.  
**Not a substitute for** [`prompts.md`](prompts.md) (raw build log), [`Phase3_PRD.md`](Phase3_PRD.md) (mentor-approved scope and the decisions Awais pushed back on: hand-rolled core, no auth, scope trims), or [`benchmark/REPORT.md`](benchmark/REPORT.md) (scored numbers). This doc turns those into a short story.

---

## What I built

secondpass is a personal Security + Architecture review agent. It runs Semgrep and an LLM logic/authorization pass, then an Architecture pass, under one Supervisor. Findings share one schema and one confidence gate. Security can pull curated lessons from Chroma. Every human accept/reject goes into SQLite. Tavily web search is optional. You can run it from the CLI, FastAPI jobs, a Vite dashboard, or MCP `review_code`.

The claim is narrow on purpose: a second pass over *my* recurring mistakes, measured on planted fixtures and a small real-world mini-suite. It is not a full AppSec product.

---

## What AI did well

**Fast on clear, bounded tasks.** Once the PRD named the seam (provider switch, CLI panels, FastAPI submit/poll, schema + gate helpers, benchmark runners, dashboard wiring), Cursor agents filled in a lot of plumbing and tests quickly.

**Prompt + hard filter works.** Soft Architecture smells, authz bleed, reverse bleed, weak structure claims, and adjacency/import-edge false positives all taught the same lesson: a prompt alone is not enough. Hard drop/cap helpers are what moved precision. AI helped draft prompts and filter tests. I kept one rule: if a wrong accept is costly, do not trust the model to “just obey.”

**Semgrep and logic-review together.** Making them complementary (not either/or) was right. On the real-world mini-suite, Semgrep alone scored **0/4** recall on the same four bugs logic-review caught. AI helped add the inconclusive path so a rate limit no longer looks like “clean.”

**Two providers, labeled scores.** Running the same suites on Groq and OpenAI, and refusing to merge unlabeled scores, turned a footnote into evidence. Real-world Security hit **1.0/1.0** on both (N=4). Architecture did not: Groq planted **1.0/1.0**, OpenAI **1.0/0.5** with label/evidence gaps. That gap is one of the most honest results in the project.

---

## Where AI failed (and what I did about it)

### 1. Soft Architecture confidence on weak smells

Early Architecture accepted soft findings on real project files (for example, inventing a naming rule that is intentional in this repo). Self-reported confidence ≥80 put weak claims in the accepted bucket. **Fix:** tighter prompt + hard drop/cap for soft smells. Treat the gate as triage, not truth.

### 2. Four Architecture false-positive modes in sequence

| # | Mode | What went wrong | Harden |
| ---: | --- | --- | --- |
| 1 | Soft smell / invented convention | Weak smells accepted at gate | Prompt + drop/cap (§21) |
| 2 | Authz / category bleed | Architecture re-labeled ownership bugs as layering | Prompt + `is_security_category_bleed` (§25) |
| 3 | Attribution bleed | Cross-file context: sibling’s bug blamed on the target | `is_off_target_finding` (§31) |
| 4 | Reverse bleed + invented structure + adjacency/import-edge | Security inventing architecture labels; stdlib treated as layers; co-located / framework FPs | Mirror bleed filter (§32); insufficient-structure (§33); package gate + resolved-project import edge (§38/§39) |

Each fix exposed the next failure mode. Saying “Architecture is done” after one harden would have been wrong.

### 3. Sampling noise mistaken for product signal

Before pinning `temperature=0`, the same `notes_idor` fixture swung on accepted count (about 1-3) and confidence near the gate. **Fix:** pin temperature and log success/fallback so “using 0.0” is seen in the log, not assumed. **Humility:** lower variance is not the same as calibrated confidence. A model can be stably wrong at 90%.

### 4. Trusting agent summaries as verification

An early checklist claim (“benchmark 1.0/1.0, done”) rested on a pasted agent summary. An external review forced me to re-run and read the raw ScoreReport myself. The number happened to be correct. The process was not.

Same lesson, later and more concrete: when OpenAI scored an Architecture FN on `checkout_handler`, Cursor suggested assumption-based fixes (soften the evidence filters; blame multi-file parallelism). I opened the audit trail instead. The model *had* returned a real `layering_violation` JSON; every structured row was dropped by post-filters; the panel then said green “clean.” Softening filters was the wrong chase. The product fix was honesty reporting: **Evidence bar not met** / `claim_unverified`, so the summary no longer contradicts the audit log.

**Lesson:** if a claim lets me skip work, or a suggested fix rests on a guess, I re-check from tool output and the audit trail myself.

### 5. Benchmark methodology bug (inconclusive ≠ FN)

The product correctly marked rate-limited logic-review as **inconclusive**. The scorer still treated empty predictions + expected ground truth as a normal **false negative**. That produced a misleading Security Groq recall of **0.75** on an incomplete run. Architecture also lacked a real inconclusive path for the same failure. **Fix:** drop inconclusive fixtures from both the ground-truth denominator and the predictions; persist per-file inconclusive/coverage; re-measure. Clean Groq Security reconfirm: **1.0/1.0** with 0 inconclusive. Catching a bug in *my own scoring* mattered as much as catching bugs in fixtures.

### 6. Provider-dependent Architecture labels

Same fixtures, temperature=0, different providers: OpenAI can mislabel or fail the evidence bar where Groq accepts the planted finding. AI did not make Architecture provider-agnostic. Measurement made the gap visible. Deploy and demo claims stay labeled by provider.

A sharper caveat than “maybe Groq benefited from practice”: the hard filters are provider-agnostic *code*, but the filter thresholds and evidence-bar strictness were validated mostly against how Groq phrases evidence, because that is where the iterate-measure-fix loop mostly ran. OpenAI’s Architecture failures were consistently “wrong label or evidence didn’t clear the bar,” not “missed the bug.” That pattern fits a bar tuned to one model’s phrasing style penalizing a different, still-plausible phrasing from another model.

---

## Lessons learned

1. **Measure before stacking tools.** Adding Semgrep packs or swapping models without a miss to explain is checklist theater. Fixtures and ScoreReports decide the next harden.
2. **Hard guards beat prompt hope** when wrong accepts are costly. Same idea from near-duplicate Chroma saves through Architecture filters. When evidence is weak, prefer honest “bar not met” over silently softening the bar.
3. **Coverage honesty is a product feature.** Inconclusive is not clean, and neither is needs_review. Claimed-but-filtered is not clean either. If the UI or scorer collapses those, you will lie to yourself in demos and reports.
4. **Human-gated memory is the closed loop.** Gate-accepted is not human-accepted. REJECT stays SQLite-only. ACCEPT may promote to Chroma. The Supervisor never auto-saves.
5. **Be honest about small N.** Planted 1.0/1.0 and real-world N=4 show progress on *these* shapes. They are not calibrated accuracy on arbitrary repos. Confidence buckets are a method; today’s scored suites still sit high, so we have not shown discrimination across buckets yet.
6. **AI speeds implementation and can mislead on unverified claims.** The internship value was learning when to trust the agent, when to re-run the benchmark, and when to write a hard filter.

---

## How this maps to the panel

| Panel ask | Where it lives |
| --- | --- |
| Core idea / problem | README, opening of live demo |
| Mentor-approved scope | [`Phase3_PRD.md`](Phase3_PRD.md) |
| How AI was used effectively | This reflection + slide “How AI was used” + `prompts.md` for the raw trail |
| Where it failed | Four Architecture modes, temperature humility, scorer inconclusive bug, audit-trail check vs assumed filter softens |
| Demo | Live: [`DEMO.md`](DEMO.md); Drive video: [`DEMO_VIDEO.md`](DEMO_VIDEO.md) |
| Numbers | [`benchmark/REPORT.md`](benchmark/REPORT.md) |

For this week, running locally (venv) or fully containerized meets the written bar. Public hosting is optional and not required to expose keys. A local `pip install -e .` CLI entry point is a cheap, safe follow-up after rehearsal. Publishing to public PyPI/npm is a separate, post-internship idea (name, versioning, irreversible publish), not a pre-deadline requirement.
