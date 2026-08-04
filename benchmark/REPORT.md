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

### Real-world Security mini-suite (OpenAI)

Separate suite (`benchmark/real_world/`, `ground_truth_real_world.json`) — **not** folded into the planted Security ground truth. Four provenance-backed vulnerable cases + four fixed/clean counterparts (see `manifest.json`).

| Metric | Value |
| --- | --- |
| True positives | **4** |
| False positives | **0** |
| False negatives | **0** |
| Precision | **1.0** |
| Recall | **1.0** |

| Vulnerable file | Predicted | Expected |
| --- | --- | --- |
| `aiohttp_static_traversal_vulnerable.py` | `path_traversal` | `path_traversal` (CVE-2024-23334) |
| `gitpython_clone_command_injection_vulnerable.py` | `command_injection` | `command_injection` (CVE-2022-24439) |
| `django_idor_notes_vulnerable.py` | `missing_ownership_check` | `missing_ownership_check` (teaching / CWE-639) |
| `labelstudio_hardcoded_secret_vulnerable.py` | `hardcoded_secret` | `hardcoded_secret` (CVE-2023-43791) |
| 4 fixed/clean counterparts | (none) | (none) |

**How to read this number:** N=4 is evidence that the Security path can generalize past secondpass’s own planted fixtures on these four documented, single-file cases under OpenAI — **not** a calibrated accuracy claim, and **not** evidence of reliability on arbitrary open-source repositories. Offline/Semgrep-only on the same suite scored **0/4 recall** (first measurement, `prompts.md` §41); default static rules catch none of these four shapes. Logic-review carries all of the real-world recall here.

**Plain reading:** Planted Security and Architecture (Groq) both match their best recorded scores. Cross-worker is clean on both directions under Groq. Real-world OpenAI measurement repeats the §41 1.0/1.0 on N=4 without retuning. Earlier OpenAI Architecture label drift on `low_level_persistence_client` (2026-08-02 A/B) remains a known provider gap if OpenAI is the deploy provider for Architecture — this refresh did not re-run Architecture on OpenAI.

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

## 5. Files touched in this documentation step

- `benchmark/results/final_20260804_20260804.json` (new)
- `benchmark/results/architecture_final_20260804_20260804.json` (new)
- `benchmark/results/cross_worker_final_20260804_20260804.json` (new)
- `benchmark/results/real_world_final_20260804_20260804.json` (new)
- `benchmark/REPORT.md` (this file; refreshed)

No application code under `app/` was modified for this step.
