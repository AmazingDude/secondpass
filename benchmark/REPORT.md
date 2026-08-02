# secondpass — Detection-quality benchmark report

**Date:** 2026-08-02  
**Purpose:** Single written record of the reliability measurement journey and a fresh final run against the current hardened codebase.  
**Scope:** Measurement and documentation only — no application code changes in this step.

**Final-run artifacts (do not overwrite priors):**

| Suite | Label / file |
| --- | --- |
| Security own-suite (+ standing clean rows on Architecture fixtures) | `benchmark/results/final_20260802_20260802.json` |
| Architecture own-suite (OpenAI — primary final run) | `benchmark/results/architecture_final_20260802_20260802.json` |
| Architecture own-suite (Groq A/B — same code/fixtures/temp=0) | `benchmark/results/architecture_groq_ab_20260802_20260802.json` |
| Live cross-worker bleed checks | `benchmark/results/cross_worker_final_20260802_20260802.json` |

Primary final-run provider: `LLM_PROVIDER=openai` (model field in results JSON is `null` — default for that provider). Controlled follow-up A/B used `LLM_PROVIDER=groq` only (no app code changes); see §4.

Scoring convention (unchanged across the project): **accepted** findings only (confidence gate ≥ threshold), unless a run explicitly used `--include-needs-review`.

---

## 1. Final summary (2026-08-02)

| Suite | TP | FP | FN | Precision | Recall | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Security own-suite** | 4 | 0 | 0 | **1.0** | **1.0** | Planted bugs: IDOR, command injection, hardcoded secret, path traversal. Clean ownership + 6 Architecture fixtures expected empty — all clean. |
| **Architecture own-suite (OpenAI)** | 1 | 1 | 1 | **0.5** | **0.5** | `checkout_handler` correct. `low_level_persistence_client` predicted `layering_violation` instead of `dependency_direction` → 1 FP + 1 FN. |
| **Architecture own-suite (Groq A/B)** | 2 | 0 | 0 | **1.0** | **1.0** | Same fixtures, same code, temperature=0. `low_level_persistence_client` → `dependency_direction` (matches §31/§32). |
| **Cross-worker (live)** | — | — | — | status **ok** | — | Security: 0 accepted / 0 needs_review on all 6 Architecture fixtures. Architecture: no *authz*/security-category bleed on Security fixtures; however Architecture still **accepted 1** non-authz finding on `hardcoded_secret.py` (secret framed as “architectural”) — check passes because that failure mode is out of the authz-bleed assert. |

**Plain reading:** Security final numbers match the best recorded Security scores. Architecture under OpenAI is **worse** than the post-§31/§32 Groq record of **1.0 / 1.0**. A same-day Groq re-run on the identical codebase restored **1.0 / 1.0** with the correct `dependency_direction` label — so this is a **confirmed provider-dependent label split**, not a §33 code regression on Groq.

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
| **Final re-run (this report)** | — | **1.0 / 1.0** | OpenAI **0.5 / 0.5**; Groq A/B **1.0 / 1.0** | See §1 / §4. OpenAI mislabels dependency as layering; Groq matches §31/§32. |

**Archive caveat (honesty):** `security_arch_bleed_fix_20260801.json` on disk scores **recall 0.75** because `path_traversal.py` hit a Groq **TPD 429** mid-run (`RateLimitError`), not because the model missed the bug. §32’s written claim of 1.0/1.0 reflects the intended completed run / narrative in `prompts.md`, not that incomplete artifact. The 2026-08-02 final Security run completed all fixtures without that error.

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

Measured Architecture inventing `layering_violation` @ ≥80 on tiny Security fixtures (`ops_shell`, `path_traversal`) by treating stdlib imports as layer boundaries — a third failure mode (not soft smell §21, not authz bleed §25). Fixed with an `INSUFFICIENT STRUCTURE` prompt rule plus `is_insufficient_structure_claim` (drop layering/dependency claims when the target has no first-party imports). Manual verify kept real Architecture fixtures’ findings and cleared the stdlib-only FPs on those Security files. §33 did **not** re-publish a full Architecture own-suite ScoreReport in `prompts.md`; the 2026-08-02 final run is the first post-harden full own-suite number in this report, and it is not 1.0/1.0.

---

## 4. Known limitations

**Suite size (narrow).**

| Class / role | Count in current ground truth |
| --- | ---: |
| Security planted bug classes | **4** (one fixture each: ownership/IDOR, command injection, hardcoded secret, path traversal) |
| Security clean control | **1** |
| Security expected-clean Architecture fixtures (reverse-bleed rows) | **6** |
| Architecture planted bug classes | **2** (layering, dependency direction) |
| Architecture clean control | **1** |

One fixture per bug class is enough to catch regressions of the exact planted pattern; it is **not** enough to claim class-wide robustness (variant phrasings, multi-file IDOR, framework-specific injection, etc.).

**Not covered (examples).** XSS, SSRF, CSRF, SQL injection, insecure deserialization, authn/session flaws, crypto misuse, race conditions, prototype pollution, supply-chain / dependency CVEs, multi-tenant isolation beyond the single IDOR pattern, non-Python languages in the scored suite, and subtle “almost correct” ownership checks. Architecture GT does not score naming, duplication, or package-boundary cases beyond the two planted structural bugs. Cross-worker asserts **authz** bleed and “Security clean on Architecture fixtures,” not “Architecture never invents any finding on Security fixtures.”

**Confidence calibration caveat (logged §23–§24).** Pinning temperature reduced run-to-run variance of accepted counts and confidence values. That does **not** mean confidence is calibrated to true positive probability. A model can be stable and wrong. The gate threshold (e.g. 80) remains a product choice, not a validated probability cut.

**Architecture label split is provider-dependent (confirmed A/B, 2026-08-02).** Same fixtures, same temperature=0 path, no application code changes between the two runs:

| Run | Provider | `low_level_persistence_client` predicted | Score |
| --- | --- | --- | --- |
| `architecture_final_20260802_20260802.json` | openai | `layering_violation` (expected `dependency_direction`) | P/R **0.5 / 0.5** |
| `architecture_groq_ab_20260802_20260802.json` | groq | `dependency_direction` | P/R **1.0 / 1.0** (matches §31/§32) |

This is **not** evidence that §33’s insufficient-structure filter broke Architecture on Groq. It **is** evidence that OpenAI’s default model for this setup reasons about that fixture differently (wrong finding_type) than Groq. Do not treat Architecture 1.0/1.0 as provider-invariant. OpenAI label discipline on this fixture remains an open product/reliability gap if OpenAI is the deploy provider; it is not an unresolved “did §33 regress Groq?” discrepancy.

**Incomplete / noisy archive runs.** Prefer `prompts.md` + successful result JSONs for timeline cells. Incomplete runs (e.g. rate-limited `security_arch_bleed_fix_20260801.json`) must not be cited as true recall regressions.

**Cross-worker residual noise.** Final live cross-worker `status: ok`, but Architecture still accepted a finding on `hardcoded_secret.py` framed as architecture. That is outside the authz-bleed filter and is still noise a panel should hear about.

---

## 5. Files touched in this documentation step

- `benchmark/results/final_20260802_20260802.json` (new)
- `benchmark/results/architecture_final_20260802_20260802.json` (new)
- `benchmark/results/architecture_groq_ab_20260802_20260802.json` (new; Groq A/B)
- `benchmark/results/cross_worker_final_20260802_20260802.json` (new)
- `benchmark/REPORT.md` (this file)

No application code under `app/` was modified for this step.
