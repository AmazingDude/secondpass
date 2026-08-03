# Real-world Security mini-suite

Purpose: prove secondpass's Security path has *some* positive value on
documented, real (not planted) vulnerable code — not just its own synthetic
fixtures — while being explicit about how small and narrow this check is.

## Scope and limits (read before citing these numbers anywhere)

- **4 vulnerable cases, single-file, Security-only.** This is not a claim of
  general reliability on arbitrary open-source code. It covers exactly the
  four finding types already in `benchmark/ground_truth.json`
  (`path_traversal`, `command_injection`, `missing_ownership_check`,
  `hardcoded_secret`) — it does not add new bug classes.
- **Not folded into the main benchmark.** `benchmark/ground_truth.json` and
  `app/benchmark_run.py` are untouched. This suite has its own ground truth
  (`benchmark/ground_truth_real_world.json`) and runner
  (`app/benchmark_run_real_world.py`) so the original suite's history stays
  comparable.
- **Provenance, not invented labels.** Every vulnerable case cites a real
  CVE/GHSA advisory or a documented public teaching write-up, with the
  source repo, commit/tag, license, and a URL. See `manifest.json` for the
  full citation per case, including an `adaptation_note` field that is
  honest about what was copied verbatim vs. reconstructed for this suite
  (e.g. the `django_idor_notes` HTML strings were re-wrapped because the
  fetch tool used to pull the file stripped angle brackets; the
  `labelstudio_hardcoded_secret` surrounding settings lines are
  reconstructed boilerplate around a verbatim `SECRET_KEY` line quoted from
  the advisory).
- **3 of 4 cases have a real fixed/clean counterpart** from the same
  upstream project (aiohttp, GitPython, django-security-lab all ship an
  official fix). The `labelstudio_hardcoded_secret` case uses a
  representative env-var clean control instead, because the actual
  post-fix `settings.py` diff wasn't verified against upstream for this
  suite (see `manifest.json`'s `adaptation_note` for that case).
- **Single-file scope by construction.** Each vulnerable file was chosen so
  the bug is fully legible from that one file/function (see
  `why_visible_single_file` in `manifest.json`). Cross-file / Architecture
  claims are out of scope for this suite.
- **Small N.** 4 vulnerable + 4 clean/fixed = 8 files total. A handful of
  files is evidence, not statistical proof — precision/recall here should be
  read as "does the pipeline generalize past its own fixtures at all,"
  not as a calibrated accuracy claim.

## Files

- `manifest.json` — one entry per case with full provenance.
- `../ground_truth_real_world.json` — `{file_path, finding_type}` ground
  truth, same shape as the main suite's `ground_truth.json`.
- `cases/*.py` — the actual vulnerable/fixed/clean source files.

## Running

```bash
python -m app.benchmark_run_real_world --offline   # Semgrep-only, no API key
python -m app.benchmark_run_real_world             # full pipeline (needs LLM key)
```

Results are written to `benchmark/results/real_world_<date>.json` — never
overwriting the main suite's `baseline_*`/etc. result files.
