# secondpass — Detection-quality benchmark report

**Date:** 2026-08-04 (refreshed; prior snapshot 2026-08-02)  
**Purpose:** Single written record of the reliability measurement journey and a fresh final run against the current, fully hardened codebase (post adjacency/import-edge Architecture harden, Security additive/inconclusive coverage, and provenance real-world mini-suite).  
**Scope:** Measurement and documentation only — no application code changes in this step.

**Final-run artifacts (do not overwrite priors):**

| Suite | Label / file | Provider |
| --- | --- | --- |
| Security own-suite (+ standing clean rows on Architecture fixtures) | `benchmark/results/final_20260804_20260804.json` | groq |
| Architecture own-suite | `benchmark/results/architecture_final_20260804_20260804.json` | groq |
| Live cross-worker bleed checks | `benchmark/results/cross_worker_final_20260804_20260804.json` | groq |
| Real-world Security mini-suite (provenance CVE/teaching cases) | `benchmark/results/real_world_final_20260804_20260804.json` | openai |

Prior 2026-08-02 artifacts (`final_20260802_*`, `architecture_final_20260802_*`, `architecture_groq_ab_20260802_*`, `cross_worker_final_20260802_*`) and the first real-world measurement (`real_world_20260803.json`) remain on disk for timeline comparison.

Scoring convention (unchanged across the project): **accepted** findings only (confidence gate ≥ threshold), unless a run explicitly used `--include-needs-review`.

**Claim guardrail:** This report claims only the measured classes, providers, and single-file scope actually tested. It does **not** claim reliability for arbitrary real-world repositories.

---

## 1. Final summary (2026-08-04)

| Suite | TP | FP | FN | Precision | Recall | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Security own-suite (Groq)** | 4 | 0 | 0 | **1.0** | **1.0** | Planted bugs: IDOR, command injection, hardcoded secret, path traversal. Clean ownership + 6 Architecture fixtures expected empty — all clean. |
| **Architecture own-suite (Groq)** | 2 | 0 | 0 | **1.0** | **1.0** | `checkout_handler` → `layering_violation`; `low_level_persistence_client` → `dependency_direction`; clean control empty. Matches §31/§32 Groq record. |
| **Cross-worker (live, Groq)** | — | — | — | status **ok** | — | Security: 0 accepted / 0 needs_review on all 6 Architecture fixtures. Architecture: 0 accepted / 0 needs_review on all 5 Security fixtures (including `hardcoded_secret.py`, which had residual non-authz Architecture noise on the 2026-08-02 OpenAI cross-worker run). |

### Real-world Security mini-suite — OpenAI vs Groq A/B

Separate suite (`benchmark/real_world/`, `ground_truth_real_world.json`) — **not** folded into the planted Security ground truth. Four provenance-backed vulnerable cases + four fixed/clean counterparts (see `manifest.json`).

| Provider | Model (default) | Date | TP | FP | FN | Precision | Recall | Results file |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| **openai** | gpt-4o-mini | 2026-08-04 / confbucket 2026-08-08 | 4 | 0 | 0 | **1.0** | **1.0** | `confbucket_realworld_20260808.json` (also `real_world_final_20260804_…`) |
| **groq** | llama-3.3-70b-versatile | 2026-08-08 A/B | 4 | 0 | 0 | **1.0** | **1.0** | `ab_realworld_groq_20260808.json` |

| Vulnerable file | OpenAI predicted | Groq predicted | Expected |
| --- | --- | --- | --- |
| `aiohttp_static_traversal_vulnerable.py` | `path_traversal` | `path_traversal` | `path_traversal` (CVE-2024-23334) |
| `gitpython_clone_command_injection_vulnerable.py` | `command_injection` | `command_injection` | `command_injection` (CVE-2022-24439) |
| `django_idor_notes_vulnerable.py` | `missing_ownership_check` | `missing_ownership_check` | `missing_ownership_check` (teaching / CWE-639) |
| `labelstudio_hardcoded_secret_vulnerable.py` | `hardcoded_secret` | `hardcoded_secret` | `hardcoded_secret` (CVE-2023-43791) |
| 4 fixed/clean counterparts | (none) | (none) | (none) |

Temp=0 confirmed on the Groq A/B live logs. Do **not** merge these into one unlabeled P/R — both providers hit 1.0/1.0 on N=4 independently.

**How to read this number:** N=4 is evidence that the Security path can generalize past secondpass’s own planted fixtures on these four documented, single-file cases under **both** OpenAI and Groq — **not** a calibrated accuracy claim, and **not** evidence of reliability on arbitrary open-source repositories. Offline/Semgrep-only on the same suite scored **0/4 recall** (first measurement, `prompts.md` §41); default static rules catch none of these four shapes. Logic-review carries all of the real-world recall here.

**Plain reading:** Planted Security and Architecture (Groq) both match their best recorded scores when runs complete without rate limits. Cross-worker is clean on both directions under Groq. Real-world OpenAI and Groq A/B both measure **1.0/1.0** on N=4 without retuning. Earlier OpenAI Architecture label/evidence gaps remain a known provider issue (see §5 Architecture OpenAI A/B below).

---

## 2. Timeline of key interventions (from `prompts.md`, not re-derived)

Numbers below are those already logged in `prompts.md` (and the matching `benchmark/results/*.json` where cited there). Empty cells mean that suite did not yet exist or that intervention did not claim a P/R change for that suite.

| Milestone | `prompts.md` | Security P / R | Architecture P / R | What changed in the number |
| --- | --- | --- | --- | --- |
| First Security live baseline | §22 / verified §23 | **1.0 / 1.0** (2 TP / 0 FP / 0 FN; 3 fixtures) | — (no scored Architecture suite yet) | Smoke baseline on IDOR + shell + clean. |
| Temperature = 0 pin | §24 | P/R unchanged (still catching IDOR); **accepted-count variance** 1–3 → flat 2 across ≥4 runs | Still unscoped as P/R; Architecture still bleed-labeling ownership as `layering_violation` @ 90% | Reliability gain was **stability**, not precision/recall. |
| Authz bleed harden (Architecture) | §25 | Spot-check: Security IDOR kept | Spot-check: Architecture clean on `notes_idor` | Unblocked later scored Architecture work; not an Architecture GT score yet. |
| Suite expansion | §30 | **1.0 / 1.0** (4 TP; +secret, +traversal) | **0.5 / 1.0** (2 TP / 2 FP) first scored Architecture baseline | Security stayed perfect on a wider but still textbook suite. Architecture recall full; precision halved by sibling misattribution. |
| Cross-file attribution filter | §31 | unchanged (own Security suite) | **1.0 / 1.0** (2 TP / 0 FP) | Sibling-leak FPs removed; target-file attribution fixed. |
| Reverse bleed (Security on Architecture) | §32 | Own suite **1.0 / 1.0**; Architecture fixtures **0/0** findings | Own suite **1.0 / 1.0** (untouched) | Standing reverse-bleed check green. |
| Insufficient-structure harden | §33 | unchanged in logged P/R | No full own-suite P/R re-score in §33; manual: ops_shell/path_traversal Architecture → 0 accepted; real layering/dependency fixtures kept | Fixes invented layering on tiny stdlib-only Security files. |
| Final re-run (2026-08-02 report) | — | **1.0 / 1.0** (OpenAI) | OpenAI **0.5 / 0.5**; Groq A/B **1.0 / 1.0** | Provider-dependent label split on `dependency_direction` vs `layering_violation`. |
| Architecture adjacency + import-edge harden | §38 / §39 | unchanged planted P/R | Groq own-suite stayed **1.0 / 1.0**; planted fixtures kept; OSS smoke FPs (co-located siblings, isolated Django/Werkzeug) dropped | Two-layer harden after qualitative OSS smoke — not a planted-suite P/R change. |
| Security coverage (additive + inconclusive) | §40 | Planted P/R unchanged in this step; coverage semantics fixed | — | Static no longer suppresses logic-review; rate-limit/hard failure → inconclusive ≠ clean. |
| Provenance real-world mini-suite | §41 | Separate suite: live OpenAI **1.0 / 1.0** (4 TP); offline Semgrep **0 / 4** recall | — | Evidence of generalization past planted fixtures; N=4 only. |
| **Final re-run (this report)** | — | Groq **1.0 / 1.0** | Groq **1.0 / 1.0** | Real-world OpenAI **1.0 / 1.0** (N=4); cross-worker **ok**. |

**Archive caveat (honesty):** `security_arch_bleed_fix_20260801.json` on disk scores **recall 0.75** because `path_traversal.py` hit a Groq **TPD 429** mid-run (`RateLimitError`), not because the model missed the bug. §32’s written claim of 1.0/1.0 reflects the intended completed run / narrative in `prompts.md`, not that incomplete artifact. The 2026-08-02 and 2026-08-04 Security final runs completed all fixtures without that error.

---

## 3. One paragraph per intervention

### Baseline — §22 / §23

Measured live Security `review_code` against the first planted suite (`notes_idor`, `ops_shell`, `clean_ownership`) via `app/benchmark_run.py`. Result: precision **1.0**, recall **1.0** (2 TP / 0 FP / 0 FN), saved as `baseline_20260729.json` and independently re-verified as `verify_20260730_20260730.json`. Finding: the score was real on those three files, but §22/§23 already framed it as a **textbook smoke baseline**, not proof of broad detection quality. Fix at this stage: none required for P/R; the open problem was suite narrowness and (separately) run-to-run confidence thrash.

### Temperature = 0 — §24 (and logging in §25.A)

Measured the same `notes_idor` fixture repeatedly before/after pinning `temperature=0` on review LLM calls. Found accepted-count range **1–3** collapsing to a flat **2**, with Security confidence stuck at 100% when present. Fixed sampling noise as the dominant thrash source without swapping providers. Did **not** improve calibrated confidence (humility note in §24): lower variance ≠ truthful confidence — Architecture could still be “consistently wrong” at 90% on a security bug. §25.A later added temperature success/fallback logging so “using 0.0” is observed in logs, not assumed.

### Cross-file attribution — §30 measurement → §31 fix

§30 gave Architecture its first scored suite (layering, dependency-direction, clean) and expanded Security (+hardcoded secret, +path traversal). Security stayed **1.0 / 1.0**. Architecture scored **0.5 / 1.0**: both real bugs were found (recall 1.0), but each co-located fixture also attributed the sibling’s bug to itself (2 FP). §31 fixed that with prompt guidance plus deterministic `is_off_target_finding`, then re-measured **1.0 / 1.0** (`architecture_attribution_fix_20260731.json`). Side observation logged then: Security was inventing architecture-flavored labels on Architecture fixtures — deferred to §32.

### Reverse bleed — §32

Measured Security inventing labels like `bypass_of_business_rules` / `dependency_direction_violation` on Architecture fixtures that have no security bug. Fixed with logic-review prompt constraints plus `is_architecture_category_bleed`, allowlisting known security types, adding Architecture fixtures as expected-clean rows in Security ground truth, and standing checks in `app/benchmark_cross_worker.py`. After re-measure: Security own-suite **1.0 / 1.0**, zero findings on Architecture fixtures; Architecture own-suite remained **1.0 / 1.0**. Note in §32: Architecture may still emit non-authz `layering_violation` on some Security fixtures — out of that harden’s scope.

### Insufficient-structure — §33

Measured Architecture inventing `layering_violation` @ ≥80 on tiny Security fixtures (`ops_shell`, `path_traversal`) by treating stdlib imports as layer boundaries — a third failure mode (not soft smell §21, not authz bleed §25). Fixed with an `INSUFFICIENT STRUCTURE` prompt rule plus `is_insufficient_structure_claim` (drop layering/dependency claims when the target has no first-party imports). Manual verify kept real Architecture fixtures’ findings and cleared the stdlib-only FPs on those Security files. §33 did **not** re-publish a full Architecture own-suite ScoreReport in `prompts.md`; the 2026-08-02 final run was the first post-harden full own-suite number in the prior report.

### Architecture adjacency + resolved project-edge — §38 / §39

Measured during qualitative OSS smoke (§37), not the planted Architecture GT: co-located unrelated files under one directory were attached as `same_package` and justified invented `layering_violation` findings; after isolation, framework files still accepted layering when evidence cited only Django self-imports or `sys.stdout`. Fixed in two layers: §38 requires `__init__.py` before attaching directory siblings as package context (import-linked and reverse-caller context unchanged); §39 classifies imports (`ImportFact`: stdlib / resolved_project / unresolved_external) and drops `layering_violation` / `dependency_direction` unless the finding’s evidence cites a resolved project import edge involving the target. Planted Architecture fixtures kept; Groq own-suite stayed **1.0 / 1.0**. This is the fourth distinct Architecture failure mode discovered in sequence (after attribution bleed §31, reverse bleed §32, insufficient-structure §33) — a pattern worth stating plainly, not hiding.

### Security coverage semantics (additive + inconclusive) — §40

Measured three coverage seams on the Security path: (1) a Semgrep hit skipped logic-review entirely, so static and logic were mutually exclusive rather than complementary; (2) LLM rate-limit / hard chat failure returned a clean-looking empty finding set; (3) truncated logic-review input was silent in the report. Fixed without retuning detection prompts: `review_code` now runs Semgrep and logic-review additively on reviewable files; rate-limit/hard failure sets `inconclusive=True` / `logic_review_status="inconclusive"` with CLI “Review incomplete” (not “No issues found”); truncation is exposed as `source_truncated` (+ note). Planted Security P/R was not the change target and remained **1.0 / 1.0** on the later re-measure. The real-world suite (§41) makes the additive design load-bearing: Semgrep alone scored **0/4** recall on those cases; logic-review carried all detections.

---

## 4. Known limitations

**Architecture has needed hardening across four distinct discovered failure modes.** In order: cross-file attribution bleed (§31), reverse category bleed from Security onto Architecture fixtures (§32), invented layering from stdlib-as-structure (§33), and adjacency / unresolved-import edges that justified layering without a cited project dependency (§38 / §39). Each harden was reactive to a concrete false-positive mode found in measurement or OSS smoke. That sequence is a reliability pattern worth noting for a panel: Architecture precision on the planted suite improved by stacking filters, not by one-shot prompt perfection. Further unseen modes remain possible outside the measured fixtures and smoke set.

**Suite size (narrow — planted).**

| Class / role | Count in current planted ground truth |
| --- | ---: |
| Security planted bug classes | **4** (one fixture each: ownership/IDOR, command injection, hardcoded secret, path traversal) |
| Security clean control | **1** |
| Security expected-clean Architecture fixtures (reverse-bleed rows) | **6** |
| Architecture planted bug classes | **2** (layering, dependency direction) |
| Architecture clean control | **1** |

One fixture per bug class is enough to catch regressions of the exact planted pattern; it is **not** enough to claim class-wide robustness (variant phrasings, multi-file IDOR, framework-specific injection, etc.).

**Real-world mini-suite (narrow — provenance).** Four vulnerable cases + four fixed/clean counterparts; single-file Security scope only; same four finding types as the planted Security suite. Offline/Semgrep-only recall on these cases is **0/4** — logic-review carries all real-world recall. That is an honest and important characterization of what default static rules alone miss on this mix (symlink-containment bypass, unsanitized clone URL/`multi_options`, missing ORM owner scope, literal `SECRET_KEY`). N=4 is evidence of generalization past planted fixtures, **not** a calibrated accuracy claim and **not** a claim of reliability for arbitrary real-world repositories.

**Not covered (examples).** XSS, SSRF, CSRF, SQL injection, insecure deserialization, authn/session flaws, crypto misuse, race conditions, prototype pollution, supply-chain / dependency CVEs, multi-tenant isolation beyond the single IDOR pattern, non-Python languages in the scored suite, and subtle “almost correct” ownership checks. Architecture GT does not score naming, duplication, or package-boundary cases beyond the two planted structural bugs. Cross-worker asserts **authz** bleed and “Security clean on Architecture fixtures,” not “Architecture never invents any finding on Security fixtures” in every future provider/run (though the 2026-08-04 Groq live check happened to be zero on both sides).

**Confidence calibration caveat (logged §23–§24).** Pinning temperature reduced run-to-run variance of accepted counts and confidence values. That does **not** mean confidence is calibrated to true positive probability. A model can be stable and wrong. The gate threshold (e.g. 80) remains a product choice, not a validated probability cut.

**Architecture label split can be provider-dependent (confirmed A/B, 2026-08-02; still accurate).** Same fixtures, same temperature=0 path, no application code changes between those two runs:

| Run | Provider | `low_level_persistence_client` predicted | Score |
| --- | --- | --- | --- |
| `architecture_final_20260802_20260802.json` | openai | `layering_violation` (expected `dependency_direction`) | P/R **0.5 / 0.5** |
| `architecture_groq_ab_20260802_20260802.json` | groq | `dependency_direction` | P/R **1.0 / 1.0** (matches §31/§32) |
| `architecture_final_20260804_20260804.json` (this report) | groq | `dependency_direction` | P/R **1.0 / 1.0** |

This was **not** evidence that §33’s insufficient-structure filter broke Architecture on Groq. It **is** evidence that OpenAI’s default model for that setup reasoned about that fixture differently (wrong finding_type) than Groq. Do not treat Architecture 1.0/1.0 as provider-invariant. This 2026-08-04 refresh used Groq for Architecture by design and did not re-measure OpenAI Architecture; the OpenAI label-discipline gap remains open if OpenAI is the deploy provider for that worker.

**Incomplete / noisy archive runs.** Prefer `prompts.md` + successful result JSONs for timeline cells. Incomplete runs (e.g. rate-limited `security_arch_bleed_fix_20260801.json`) must not be cited as true recall regressions.

**Cross-worker residual history.** The 2026-08-02 OpenAI live cross-worker was `status: ok` on the authz-bleed asserts, but Architecture still accepted a non-authz finding on `hardcoded_secret.py`. The 2026-08-04 Groq live check shows 0 accepted on that file and on the other Security fixtures. That is an improvement under this provider/code state, not a proof that Architecture will never invent findings on Security files again.

---

## 5. Confidence-bucket precision analysis (2026-08-08, measurement/reporting only)

**Scope guardrail:** this section changes what gets *persisted and read*, not
what gets *detected*. `app/benchmark_run.py`, `app/benchmark_run_architecture.py`,
and `app/benchmark_run_real_world.py` write each individual finding's
`confidence` (and `detection_method`) into the results JSON — both as a field
on `predictions[]` and as a fuller, non-deduplicated
`per_file[*].confidence_records` list that also includes sub-gate
`needs_review` findings. The confidence gate threshold, `Finding` schema,
detection prompts, and Architecture filters were not touched.
`app/benchmark_confidence_buckets.py` buckets those persisted findings and
computes precision (hit rate against ground truth) within each bucket.

**Provider labeling (do not mix rows across providers without labeling):**
every row below names the provider that produced it. OpenAI Security and
OpenAI real-world bucket rows from earlier on 2026-08-08 are **kept**; Groq
Security and Groq real-world rows were added the same day for internal
consistency with the §1 Groq Security / real-world P/R headlines. Architecture
keeps the existing Groq bucket rows and adds an OpenAI A/B pass beside them.

| Suite | Provider | Results file | Suite P/R (accepted-only) |
| --- | --- | --- | --- |
| Security (planted) | openai | `confbucket_security_20260808.json` | 1.0 / 1.0 |
| Security (planted) | **groq** | `ab_security_groq_clean_20260809.json` | **1.0 / 1.0** (clean run; old 0.75 artifact kept — see note) |
| Architecture (planted) | groq | `confbucket_architecture_groq_20260808.json` | 1.0 / 1.0 |
| Architecture (planted) | **openai** | `ab_architecture_openai_20260808.json` | 1.0 / **0.5** (see note) |
| Real-world Security | openai | `confbucket_realworld_20260808.json` | 1.0 / 1.0 |
| Real-world Security | **groq** | `ab_realworld_groq_20260808.json` | 1.0 / 1.0 |

**Groq planted-Security — clean run (2026-08-09):** after a new Groq API
key, `--label ab_security_groq_clean` completed with **0 inconclusive
fixtures**, temp=0 confirmed in live logs. Score: **4 TP / 0 FP / 0 FN →
P=1.0 / R=1.0**. Predictions: `hardcoded_secret` @100 (`llm_reasoning`),
`missing_ownership_check` @100 (`llm_reasoning`), `command_injection` @90
(`static_rule`), `path_traversal` @100 (`llm_reasoning`). Artifact:
`ab_security_groq_clean_20260809.json`. Independent reconfirm the same night
(`ab_security_groq_clean2_20260809.json`) also **1.0 / 1.0**, 0 inconclusive.
OpenAI Security buckets are **not** retracted.

**Why the earlier 0.75 was a coverage artifact (kept on disk):**
`ab_security_groq_20260808.json` scored recall 0.75 because
`path_traversal.py` was rate-limited mid logic-review (`inconclusive —
rate limited`) and the old scorer treated empty predictions + expected GT
as a normal FN. Under the fixed scorer that same incomplete run would have
been reported as 1 excluded/inconclusive fixture on a 3-fixture-scored
denominator, not a real miss. Do not cite the archived 0.75 as model
recall.

**Correctness fix (coverage vs. false negative):** the benchmark scorer
previously had no way to tell "the model looked and found nothing" apart
from "the review never completed" — both showed up as zero predictions and
were scored as a plain false negative. `app/benchmark_run.py` (and, on the
same pattern, `app/benchmark_run_architecture.py` and
`app/benchmark_run_real_world.py`) now persist `per_file[*].inconclusive` /
`coverage_status` from the worker's own coverage signal, and **exclude**
inconclusive fixtures — both their ground-truth entry and any partial
prediction they produced (e.g. a `static_rule` hit before the LLM leg
rate-limited) — from that run's precision/recall denominator entirely. The
run is reported instead as e.g. "N fixture(s) inconclusive — excluded from
scored recall." Earlier same-day clean re-attempts (before the new key)
hit sustained rate limits and demonstrated the fix reporting zero coverage
rather than silently scoring 0.0 recall; those failed attempts are
superseded by the clean 2026-08-09 artifact above.

**Same conflation checked in the other two runners:** `app/benchmark_run_real_world.py`
already persisted `per_file[*].inconclusive` from `review_code` but, like
Security before this fix, still scored inconclusive fixtures against the
full ground truth (same silent-FN bug) — fixed with the identical
exclude-from-denominator pattern above; no real-world rate limit was hit in
the Groq/OpenAI runs on record, so this fix does not change any published
real-world number. `app/benchmark_run_architecture.py` had a **worse**
version of the same gap: `review_architecture` had no `inconclusive`
concept at all — `app/workers/architecture_worker.py`'s catch-all handler
for a failed/rate-limited LLM call returned `claim_unverified=False` with a
"could not complete" summary that was indistinguishable from an honest
clean result. Fixed on the same pattern as Security's logic-review: the
worker now catches `LLMRateLimitedError` separately, sets
`inconclusive=True` (mirrored on `coverage_status`) on both that and the
generic-failure path, and `benchmark_run_architecture.py` excludes those
fixtures from scoring the same way. No published Architecture run in this
report hit that path, so no existing Architecture number changes — this is
a latent-bug fix, not a re-score.

**OpenAI planted-Architecture note:** `checkout_handler.py` →
`claim_unverified` (LLM claimed layering; evidence bar dropped all rows) →
FN for `layering_violation`. `low_level_persistence_client.py` → accepted
`dependency_direction` @90 → TP. Clean control empty. Suite score
**P=1.0 / R=0.5**. One confidence-bucket data point (N=1), not an empty
table — still too small to read as a rate. Earlier same-day empty OpenAI
Architecture payload (`confbucket_architecture_20260808.json`) remains on
disk as prior evidence of the gap; this A/B file is the labeled OpenAI row
in the table below.

### Confidence-bucket table (real numbers, computed 2026-08-08)

| Suite | Provider | Confidence bucket | Hits / N | Precision | Sample-size note |
| --- | --- | --- | ---: | ---: | --- |
| security | openai | <70 | 0 / 0 | n/a | no data in this bucket |
| security | openai | 70-79 | 0 / 0 | n/a | no data in this bucket |
| security | openai | 80-89 | 0 / 0 | n/a | no data in this bucket |
| security | openai | 90-100 | 5 / 5 | 1.00 | N=5 — small |
| security | groq | <70 | 0 / 0 | n/a | no data in this bucket |
| security | groq | 70-79 | 0 / 0 | n/a | no data in this bucket |
| security | groq | 80-89 | 0 / 0 | n/a | no data in this bucket |
| security | groq | 90-100 | 4 / 4 | 1.00 | N=4 — small; clean run (`ab_security_groq_clean_20260809.json`) |
| architecture | groq | <70 | 0 / 0 | n/a | no data in this bucket |
| architecture | groq | 70-79 | 0 / 0 | n/a | no data in this bucket |
| architecture | groq | 80-89 | 0 / 0 | n/a | no data in this bucket |
| architecture | groq | 90-100 | 2 / 2 | 1.00 | **N=2 — too small to read as a rate** |
| architecture | openai | <70 | 0 / 0 | n/a | no data in this bucket |
| architecture | openai | 70-79 | 0 / 0 | n/a | no data in this bucket |
| architecture | openai | 80-89 | 0 / 0 | n/a | no data in this bucket |
| architecture | openai | 90-100 | 1 / 1 | 1.00 | **N=1 — too small to read as a rate**; checkout_handler claim_unverified (no confidence) |
| real_world | openai | <70 | 0 / 0 | n/a | no data in this bucket |
| real_world | openai | 70-79 | 0 / 0 | n/a | no data in this bucket |
| real_world | openai | 80-89 | 0 / 0 | n/a | no data in this bucket |
| real_world | openai | 90-100 | 4 / 4 | 1.00 | N=4 — small |
| real_world | groq | <70 | 0 / 0 | n/a | no data in this bucket |
| real_world | groq | 70-79 | 0 / 0 | n/a | no data in this bucket |
| real_world | groq | 80-89 | 0 / 0 | n/a | no data in this bucket |
| real_world | groq | 90-100 | 4 / 4 | 1.00 | N=4 — small; confidences were 100 (still 90-100 bucket) |

Reproduce with:

```bash
python -m app.benchmark_confidence_buckets --markdown \
  "security:benchmark/results/confbucket_security_20260808.json" \
  "security_groq:benchmark/results/ab_security_groq_clean_20260809.json" \
  "architecture_groq:benchmark/results/confbucket_architecture_groq_20260808.json" \
  "architecture_openai:benchmark/results/ab_architecture_openai_20260808.json" \
  "real_world:benchmark/results/confbucket_realworld_20260808.json" \
  "real_world_groq:benchmark/results/ab_realworld_groq_20260808.json"
```

**Small-sample honesty (stated plainly, not just implied by the table):**
Every non-empty bucket in these runs has **N≤5**. A precision of 1.00 from
N=1–N=5 is a description of these specific accepted/needs_review rows, **not**
a statistically meaningful calibration rate. Lower buckets (`<70`, `70-79`,
`80-89`) stayed empty on these planted/real-world passes (findings clustered
at 90 or 100). Treat every number as provider-labeled and run-specific.

**`detection_method` as a second, complementary trust signal.** Confidence
is a per-finding number from one source (a static rule's fixed default, or
the LLM's self-reported confidence). `detection_method` (`static_rule` vs
`llm_reasoning`) is orthogonal: e.g. Groq planted Security on `ops_shell.py`
accepted `command_injection` via `static_rule` @90 only in this A/B (no
second llm_reasoning row this pass). Two independent methods agreeing is a
stronger trust signal than either alone when both appear.

### What this means for slides (max 5)

- Real-world CVE mini-suite: **OpenAI and Groq both 1.0/1.0 on N=4** — cite side-by-side, do not merge.
- Planted Security: **OpenAI and Groq both 1.0/1.0** on clean runs; the earlier Groq 0.75 was a rate-limit coverage artifact (kept on disk), and the scorer now excludes inconclusive fixtures from recall instead of silently counting them as misses.
- Architecture: **Groq** still the measured full accept path (2/2); **OpenAI** A/B got 1 TP (`dependency_direction`) + 1 claim_unverified FN on layering — provider gap, not “no Architecture.”
- Confidence still clusters high (90–100); lower buckets empty on these suites — method exists; cross-bucket discrimination not shown here.
- Every bucket N≤5: say “track record on these runs,” not “calibrated confidence.”

---

## 6. Qualitative OSS confidence re-measurement (2026-08-08)

**This is not a scored benchmark.** The 10 files below are real OSS source
(werkzeug, Django, requests, itsdangerous, tqdm, tornado, python-dotenv,
humanize) with **no exhaustive formal ground truth** — they were picked for
prior qualitative smoke testing (`prompts.md` §§37–39), not as labeled
fixtures. This section reports what one full pass produced: confidence
distribution, accepted/needs_review split, `detection_method` mix, and
whether the old Architecture false-positive shape recurred. It does **not**
compute or claim precision, recall, accuracy, or calibration for this
cohort — a "previously plausibly clean" judgment is not the same thing as a
verified ground-truth label, and a new finding here could be a real issue
that simply wasn't looked for before.

**Cohort:** 10 files across 8 projects (werkzeug ×2, Django ×2, requests,
itsdangerous, tqdm, tornado, python-dotenv, humanize) — the same files
already reviewed in `smoke_test_external/` and `smoke_test_security/`.

**Provider/model/run date:** `LLM_PROVIDER=openai`, default model
`gpt-4o-mini` (`LLM_MODEL` unset), temperature 0 — confirmed live from the
run's own agent-event log: every one of the 20 file/worker runs logged
`llm temperature: using 0.0 (requested 0.0)`. Run date **2026-08-08**.
OpenAI was chosen (over Groq, the default for other suites in this report)
because the one prior isolated OSS batch that explicitly logged its
provider (`smoke_test_security/out_*.txt`, "Provider: openai") used OpenAI;
the very first (`smoke_test_external/`) batch's provider was never logged
and is not guessed here.

**Tool:** `app/benchmark_qualitative_oss.py` (new, separate from the scored
benchmark runners — it does not read or write any ground-truth file). One
run per file/worker, Security and Architecture run separately, no reruns.
Raw payload: `benchmark/results/qualitative_oss_20260808.json` (gitignored).

**Run counts:**

| Metric | Count |
| --- | ---: |
| File/worker runs | **20** (10 files × 2 workers) |
| Completed | **20** |
| Inconclusive (rate limit / logic-review failure) | **0** |
| Error | **0** |

No file was skipped, rate-limited, or silently omitted.

### Confidence-bucket occupancy by worker

| Worker | <70 | 70-79 | 80-89 | 90-100 |
| --- | ---: | ---: | ---: | ---: |
| security | 0 | 0 | 0 | 0 |
| architecture | 0 | **3** | 0 | 0 |

Security produced **zero** findings of any confidence on all 10 files
(every file was a Security zero-finding outcome — see below). Architecture
produced exactly three findings with a confidence value, **all at 79%**,
all `needs_review` (gate threshold is 80) — a genuinely different bucket
than the confidence-bucket run in §5 above (which saw everything land at
90). Read this as "this cohort naturally produced some sub-gate confidence
variety," not as a calibration result — N=3 in one bucket is still too
small to say anything about precision at 70-79.

### Accepted vs needs_review vs claim-unverified vs zero-finding

| Outcome | Count | Notes |
| --- | ---: | --- |
| Accepted findings (any worker) | **0** | No file crossed the ≥80 gate. |
| Needs-review findings | **3** | All Architecture, all `naming_convention`, all confidence 79, all `detection_method="llm_reasoning"` |
| Architecture claim-unverified (`claim_status="unverified"`) | **5** | LLM claimed an issue; evidence-bar filter dropped every structured row |
| Zero-finding (no accepted/needs_review/claim, clean) | **12** | 10 Security (all 10 files) + 2 Architecture (`itsdangerous_encoding.py`, `humanize_filesize.py`) |

`detection_method` distribution: **3/3** of the needs_review findings are
`llm_reasoning` (Architecture has no static-rule path); Security had no
findings at all this run, so there is no `static_rule` data point in this
cohort.

### Did the old Architecture layering false-positive shape recur?

**Not reproduced on this cohort/run — 0 of 10 files got an accepted
Architecture finding**, versus 6 of 10 files that had at least one
*accepted* Architecture finding in the two prior batches (4 of 5 in the
pre-harden `smoke_test_external/` batch — `werkzeug_security.py`,
`django_crypto.py`, `requests_exceptions.py`, `itsdangerous_encoding.py`,
each an accepted `layering_violation`; 2 of 5 in the
`smoke_test_security/` OpenAI batch — `werkzeug_debug_console.py` accepted
2 `layering_violation` @ 85%, `django_detail_view.py` accepted 1
`layering_violation` @ 85%). In this fresh run, every one of those same six
files either produced no architecture claim at all
(`itsdangerous_encoding.py`) or was correctly routed to `claim_unverified`
(the other five) instead of `accepted`. Say this plainly and no stronger:
**not reproduced on this cohort/run** — not "permanently fixed." One clean
pass on 10 files after a hardening sequence with four prior discovered
failure modes (§4) is evidence the fix generalizes to this cohort today, not
proof it can never recur under a different provider, run, or file.

A different, lower-stakes pattern **did** persist: sub-gate
`naming_convention` findings at 79% confidence appeared in both the old and
new runs (old: `requests_exceptions.py` JSONDecodeError naming,
`tqdm_utils.py`/`werkzeug_debug_console.py`/`humanize_filesize.py` various
naming smells; new: the same three files below). These are weak,
below-gate signals, not accepted findings, and were never the false-positive
mode the §38/§39 hardens targeted — noted for completeness, not as a
regression.

### Per-finding adjudication queue

| # | File | Type | Confidence | Evidence (summary) | Prior judgment comparison |
| --- | --- | --- | ---: | --- | --- |
| 1 | `smoke_test_external/requests_exceptions.py` | `naming_convention` | 79 (needs_review) | `JSONDecodeError` class name doesn't end in the file's usual `...Error` suffix pattern per the LLM's read | **Matches** the post-harden `out_postcheck_requests_exceptions.txt` recheck exactly (same type, same confidence, same evidence) — stable, reproducible, not new. |
| 2 | `smoke_test_external/tqdm_utils.py` | `naming_convention` | 79 (needs_review) | Local function `envwrap` shadows an `envwrap` name also imported from the third-party `envwrap` package | **New** — the old run only logged "claimed issues but produced none specific; treating as clean" with no specific claim text to compare against. The shadowing observation itself is factually accurate in the source (see `try: from envwrap import envwrap` fallback import in `tqdm_utils.py`); whether it rises to a real architecture concern is a judgment call. **Flagged for human adjudication**, not auto-accepted or auto-rejected. |
| 3 | `smoke_test_security/werkzeug_debug_console/werkzeug_debug_console.py` | `naming_convention` | 79 (needs_review) | Import `helper` (from `.repr import debug_repr, dump, helper`) reads as less descriptive than sibling imports `debug_repr`/`dump` | **Differs** from the prior run on the same file, which had 2 *accepted* `layering_violation` @ 85% (`sys.stdout` access) plus a *different* needs_review `naming_convention` @ 79% (about the `debug_repr` import, not `helper`). The accepted layering pair did not recur (see above); the naming-convention slot recurred as a category but pointed at a different symbol both times — read as the model finding *some* low-confidence naming nit on this file across runs, not the same specific claim twice. |

Claim-unverified files (`werkzeug_security.py`, `django_crypto.py`,
`tornado_process.py`, `dotenv_parser.py`, `django_detail_view.py`) are not
included in the adjudication queue above because they carry no accepted
Finding and no confidence value to adjudicate — they are recorded in
`claim_unverified_records` in the raw JSON for audit purposes only.

### Explicit no-calibration statement

**No precision, recall, accuracy, or calibration is claimed for this
cohort.** These 10 files have no exhaustive expected-finding list; "0
accepted findings" and "3 needs_review at 79%" describe what the pipeline
did on this pass, not whether those judgments are correct. The one item
that most needs a human look is adjudication row #2 above (new
`naming_convention` finding on `tqdm_utils.py`) — everything else either
matches a prior judgment or is a filtered/clean outcome consistent with the
post-harden design intent.

---

## 6b. Qualitative OSS confidence re-measurement — Groq A/B (2026-08-08)

**Same fixed cohort as §6** (10 files / 8 projects; identical paths under
`smoke_test_external/` and `smoke_test_security/`). Measurement/reporting
only — `COHORT` membership, source files, prompts, filters, and gate were
not changed. OpenAI §6 numbers above are **not** rewritten; this is a
second, labeled pass for provider comparison.

**Provider/model/run date:** `LLM_PROVIDER=groq`, model
`llama-3.3-70b-versatile` (provider default; `LLM_MODEL` unset),
temperature 0 — confirmed live: every Security and Architecture LLM call
in this pass logged `llm temperature: using 0.0 (requested 0.0)`. Run date
**2026-08-08**. Tool: `app/benchmark_qualitative_oss.py --label
qualitative_oss_groq` (runner unchanged). Raw payload:
`benchmark/results/qualitative_oss_groq_20260808.json` (gitignored; does
not clobber the OpenAI `qualitative_oss_20260808.json`).

**Run counts:**

| Metric | Count |
| --- | ---: |
| File/worker runs | **20** (10 files × 2 workers) |
| Completed | **20** |
| Inconclusive | **0** |
| Error | **0** |

Same 10 paths as §6 / `COHORT` confirmed against the OpenAI payload
(`same_paths=true`).

### Confidence-bucket occupancy by worker (Groq)

| Worker | <70 | 70-79 | 80-89 | 90-100 |
| --- | ---: | ---: | ---: | ---: |
| security | 0 | 0 | 0 | 0 |
| architecture | 0 | 0 | 0 | 0 |

### Accepted / needs_review / claim_unverified / zero-finding (Groq)

| Outcome | Count |
| --- | ---: |
| Accepted findings | **0** |
| Needs-review findings | **0** |
| Architecture claim-unverified | **0** |
| Zero-finding (Security + Architecture) | **20** |

All 10 files were Security-clean and Architecture-clean on this pass — no
emitted finding with a confidence value, and no filtered claim that
survived as `claim_unverified`.

### Side-by-side with §6 OpenAI (same cohort, same day)

| Question | OpenAI (§6) | Groq (§6b) |
| --- | --- | --- |
| (a) Accepted layering FPs appear? | **No** (0 accepted; prior accepted layering on 6/10 files not reproduced) | **No** (0 accepted) |
| (b) naming@79 cluster? | **Yes** — 3 Architecture `naming_convention` @ 79, all `needs_review` | **No** — 0 findings in any bucket |
| (c) claim_unverified count | **5** | **0** |

**Plain reading:** On this cohort/run, Groq was **quieter** than OpenAI —
empty confidence buckets, no naming@79 cluster, no claim-unverified
residue. OpenAI was noisier on soft Architecture signals (sub-gate naming +
evidence-bar claims) while both providers stayed at **0 accepted** findings
(including 0 accepted layering). Neither result is “better” in a scored
sense; this cohort still has no formal ground truth.

**Limitations (same as §6):** qualitative distribution only — no precision,
recall, accuracy, or calibration claimed. “Not reproduced on this
cohort/run” for accepted layering under both providers is **not**
“permanently fixed.” One Groq pass; no further reruns.

---

## 7. Files touched in this documentation step

- `benchmark/results/final_20260804_20260804.json` (new)
- `benchmark/results/architecture_final_20260804_20260804.json` (new)
- `benchmark/results/cross_worker_final_20260804_20260804.json` (new)
- `benchmark/results/real_world_final_20260804_20260804.json` (new)
- `app/benchmark.py` — `PredictedFinding` gained optional `confidence` /
  `detection_method` fields (informational only; matching in `evaluate` is
  unchanged)
- `app/benchmark_run.py` — `predictions_from_report_items` now carries
  confidence/detection_method through; new
  `confidence_records_from_report_items` helper feeds
  `per_file[*].confidence_records`
- `app/benchmark_run_architecture.py`, `app/benchmark_run_real_world.py` —
  same `confidence_records` addition to their `per_file` notes
- `app/benchmark_confidence_buckets.py` (new) — confidence-bucket precision
  analysis tool
- `tests/test_benchmark_run.py` — added confidence/detection_method
  propagation tests
- `tests/test_benchmark_confidence_buckets.py` (new)
- `app/benchmark_qualitative_oss.py` / `tests/test_benchmark_qualitative_oss.py`
  — qualitative OSS runner (§6 / §6b); no ground truth
- `benchmark/results/confbucket_security_20260808.json`,
  `confbucket_architecture_20260808.json` (empty OpenAI-Architecture
  evidence, kept for the gap record above),
  `confbucket_architecture_groq_20260808.json`,
  `confbucket_realworld_20260808.json` (feed §5 OpenAI / Groq Architecture rows)
- `benchmark/results/ab_realworld_groq_20260808.json`,
  `ab_security_groq_20260808.json`,
  `ab_architecture_openai_20260808.json` (2026-08-08 provider A/B; §1 + §5)
- `benchmark/results/qualitative_oss_20260808.json` (new; feeds §6, gitignored)
- `benchmark/results/qualitative_oss_groq_20260808.json` (new; feeds §6b,
  gitignored — does not clobber OpenAI payload)
- `benchmark/REPORT.md` (this file; §1 real-world A/B, §5 dual-provider
  buckets, §6 / §6b qualitative OSS)
- `DEMO_STUDY.md` — §8 note updated with §6 lesson + §6b OpenAI-vs-Groq
  panel lines

No detection logic, `Finding` schema semantics, confidence gate threshold,
or Architecture filters were modified for this step.

### Inconclusive-vs-false-negative scoring fix (2026-08-08, later pass)

- `app/benchmark_run.py` — persists `per_file[*].inconclusive` /
  `coverage_status`; excludes inconclusive fixtures' ground-truth entries
  **and** any prediction they produced from the run's precision/recall
  denominator; reports `inconclusive_fixtures` / `scoring_note` in the
  payload instead of silently inflating false_negatives
- `app/benchmark_run_architecture.py`, `app/benchmark_run_real_world.py` —
  same exclude-from-denominator pattern applied (real-world already
  persisted `inconclusive`; Architecture did not have the concept at all
  until this pass — see below)
- `app/workers/architecture_worker.py` — the failed/rate-limited LLM-call
  handler now distinguishes `LLMRateLimitedError` from other exceptions and
  sets `inconclusive=True` on both paths, instead of returning a result
  indistinguishable from an honest clean review
- `app/agent.py` — `review_architecture` / `build_architecture_review_output`
  propagate `inconclusive` through to the report dict and
  `ReviewResult.coverage_status`
- `tests/test_benchmark_run.py` — two new focused tests: an inconclusive
  fixture with zero predictions must not inflate `false_negatives`, and an
  inconclusive fixture that still produced a partial prediction must not
  leak into `false_positives`
- `benchmark/results/ab_security_groq_clean_20260809.json` (new; clean Groq
  planted-Security A/B after new API key — **4 TP / 0 FP / 0 FN**, 0
  inconclusive; feeds §5 Groq Security P/R + buckets). Earlier incomplete
  `ab_security_groq_20260808.json` (recall 0.75 coverage artifact) kept on
  disk as evidence of the original conflation bug.

Still no detection prompts, filters, `Finding` schema, or confidence gate
changes in this pass — scoring/reporting correctness only.
