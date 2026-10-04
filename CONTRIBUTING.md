# Contributing

Start with a focused change and explain which problem it solves. For bug fixes,
include a regression test that fails before the fix. Keep generated reports,
credentials, local databases and model traces out of pull requests.

## Development setup

CI checks Linux with Python 3.12 and Node.js 24. Use those versions to match CI;
the package's declared Python minimum is not a tested platform matrix.

From a fresh checkout, create and activate a virtual environment:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, use `py -3.12 -m venv .venv` followed by
`.\.venv\Scripts\Activate.ps1` instead. Then install the app and test tools:

```sh
python -m pip install -e ".[dev,memory]"
```

The `dev` extra supplies test/build tools; `memory` installs ChromaDB for the
lesson-store tests. A normal `pip install -e .` installs runtime dependencies
without lesson memory. `pip install -r requirements.txt` preserves the full
runtime profile with memory, using `pyproject.toml` as the dependency source.
Python dependencies are not locked yet: fresh installs can resolve newer
versions, and CI checks that the resolved dependencies are compatible.

Install frontend dependencies from the lockfile:

```sh
cd web
npm ci
cd ..
```

## Checks before opening a PR

From the repository root, with the virtual environment active:

```sh
python -m pip check
python -m compileall -q app tests
python -m pytest -q --tb=short
```

Run one test file or case while developing:

```sh
python -m pytest tests/test_review_response_validation.py -q
python -m pytest tests/test_review_response_validation.py -k malformed -q
```

The test suite uses mocked model calls; it does not need provider keys, a running
API server or a frontend server. Use a clean checkout without a personal `.env`
when reproducing CI. Dependency installation needs network access. Live model
benchmarks are separate from this suite and can incur provider charges.

For the frontend:

```sh
cd web
npm run lint
npm test
npm run build
```

The build includes TypeScript checking. The frontend tests cover the audit
pipeline timeline; for other UI changes, also check the affected flow in a
browser and describe what you checked in the PR.

The saved-review to Memory regression runs in headless Chromium using fixed HTTP
fixtures. It needs no backend or provider keys and writes no real outcomes. From
`web/`, after `npm ci`, install the browser matching the lockfile, build with the
default API URL (`http://127.0.0.1:8000`), and run:

```sh
npx --no-install playwright-cli install-browser chromium --only-shell
npm run build
npm run test:browser
```

These commands also work in PowerShell. On Linux, add `--with-deps` to the browser
install command for OS libraries; downloads need network access. The CLI is an
exact development-only pin and currently includes an alpha Playwright engine:
use its installer rather than a different version's browser binaries.

`test:browser` serves the existing production build on `127.0.0.1:5174`; keep
that port free. It uses an isolated browser, bounds CLI commands, and closes its
own browser/preview on success or failure. Assertion errors exit nonzero. CLI
artifacts stay under local `output/playwright/browser-regression/` and are not
uploaded. This separate command runs in frontend CI alongside `npm test`, which
still runs the pipeline tests. It also checks SIGTERM cleanup on POSIX systems;
that lifecycle test is skipped on Windows, which does not deliver that signal.

## CI and review

Pull requests and pushes to `main` run separate Python and frontend jobs in
[the CI workflow](.github/workflows/ci.yml). Dependency downloads are cached,
but each job installs its dependencies; an installed environment is not reused.
The Python check covers syntax, dependency compatibility and tests, not style
linting or static type checking.

Jobs use read-only repository permissions, immutable action references and no
provider secrets. Keep PR checks on the unprivileged `pull_request` event. When
updating an action, verify the full commit SHA against its official release and
update the version comment too; see GitHub's
[secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

Describe the problem, any important tradeoff, and your test results in the PR.
Passing CI does not publish a release or deploy the application, and making
checks required for merging is a separate repository setting.
