"""Provenance-backed real-world case: GitPython clone command/argument injection.

Source repo:      https://github.com/gitpython-developers/GitPython
Vulnerable tag:    3.1.29 (last vulnerable release)
File (upstream):   git/repo/base.py, Repo._clone classmethod
License:           BSD-3-Clause
Advisory:          CVE-2022-24439 / GHSA-hcpj-qp55-gfph
                    https://github.com/advisories/GHSA-hcpj-qp55-gfph
Disclosure issue:  https://github.com/gitpython-developers/GitPython/issues/1515
Fix PRs:           https://github.com/gitpython-developers/GitPython/pull/1516
                    https://github.com/gitpython-developers/GitPython/pull/1518

Why the bug is visible in this single file/function alone:
`_clone` takes a caller-supplied `url` (and `multi_options`) and passes them
straight into `git.clone(...)`, which shells out to the real `git` binary.
There is no validation that `url` isn't an `ext::` transport helper (which
git will happily exec as a shell command) and no denylist on dangerous
`multi_options` flags like `--upload-pack=<cmd>`. Both the disclosed PoCs
(`Repo.clone_from('ext::sh -c touch% /tmp/pwned', ...)` and
`r.clone('foo', ..., multi_options=['--upload-pack="touch /tmp/pwn"'])`)
exploit exactly this function — nothing outside it is required.
"""

from __future__ import annotations

import logging
import os.path as osp
import shlex
from typing import Any, Callable, List, Optional, Type, Union

log = logging.getLogger(__name__)


class Repo:
    """Trimmed stand-in for GitPython's Repo class (only _clone kept)."""

    @classmethod
    def _clone(
        cls,
        git: "Git",
        url,
        path,
        odb_default_type: Type[Any],
        progress: Optional[Callable] = None,
        multi_options: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> "Repo":
        odbt = kwargs.pop("odbt", odb_default_type)

        if not isinstance(path, str):
            path = str(path)

        clone_path = Git.polish_url(path) if Git.is_cygwin() and "bare" in kwargs else path
        sep_dir = kwargs.get("separate_git_dir")
        if sep_dir:
            kwargs["separate_git_dir"] = Git.polish_url(sep_dir)
        multi = None
        if multi_options:
            multi = shlex.split(" ".join(multi_options))

        # BUG: `url` and `multi` reach the `git clone` subprocess call with no
        # protocol allowlist and no check against dangerous flags such as
        # --upload-pack=<cmd> or ext::<shell command>. A caller-controlled URL
        # or multi_options value results in arbitrary command execution.
        proc = git.clone(
            multi,
            Git.polish_url(str(url)),
            clone_path,
            with_extended_output=True,
            as_process=True,
            v=True,
            universal_newlines=True,
            **kwargs,
        )

        (stdout, stderr) = proc.communicate()
        cmdline = getattr(proc, "args", "")
        log.debug("Cmd(%s)'s unused stdout: %s", cmdline, stdout)

        if not osp.isabs(path):
            path = osp.join(git._working_dir, path) if git._working_dir is not None else path

        repo = cls(path, odbt=odbt)
        repo.git.update_environment(**git.environment())

        if repo.remotes:
            with repo.remotes[0].config_writer as writer:
                writer.set_value("url", Git.polish_url(repo.remotes[0].url))
        return repo


class Git:
    """Minimal stub so the excerpt above stays self-contained for review."""

    @staticmethod
    def polish_url(url: str) -> str:
        return url

    @staticmethod
    def is_cygwin() -> bool:
        return False

    def clone(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
