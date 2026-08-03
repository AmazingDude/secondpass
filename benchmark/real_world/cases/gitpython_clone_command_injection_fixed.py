"""Provenance-backed real-world case: GitPython clone command/argument injection — FIXED.

Source repo:      https://github.com/gitpython-developers/GitPython
Fixed tag:         3.1.30
File (upstream):   git/repo/base.py, Repo._clone classmethod
License:           BSD-3-Clause
Advisory:          CVE-2022-24439 / GHSA-hcpj-qp55-gfph
                    https://github.com/advisories/GHSA-hcpj-qp55-gfph
Fix PRs:           https://github.com/gitpython-developers/GitPython/pull/1516
                    https://github.com/gitpython-developers/GitPython/pull/1518

This is the clean/fixed counterpart of gitpython_clone_command_injection_vulnerable.py.
The fix adds `Git.check_unsafe_protocols` (rejects `ext::`/`fd::` style URLs
unless explicitly allowed) and `Git.check_unsafe_options` (denylists flags
like `--upload-pack`) before the subprocess call, plus a `--` separator so
a crafted url/path can't be parsed as an option. Used as a clean control:
secondpass should not accept a command_injection finding on this file.
"""

from __future__ import annotations

import logging
import os.path as osp
import shlex
from typing import Any, Callable, List, Optional, Type

log = logging.getLogger(__name__)


class Repo:
    """Trimmed stand-in for GitPython's Repo class (only _clone kept)."""

    unsafe_git_clone_options = [
        "--upload-pack",
        "-u",
        "--config",
        "-c",
    ]

    @classmethod
    def _clone(
        cls,
        git: "Git",
        url,
        path,
        odb_default_type: Type[Any],
        progress: Optional[Callable] = None,
        multi_options: Optional[List[str]] = None,
        allow_unsafe_protocols: bool = False,
        allow_unsafe_options: bool = False,
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

        # FIX: reject unsafe transport protocols (ext::, fd::, ...) and
        # unsafe multi_options flags (--upload-pack, -c, ...) before the url
        # or options ever reach the git subprocess call.
        if not allow_unsafe_protocols:
            Git.check_unsafe_protocols(str(url))
        if not allow_unsafe_options and multi_options:
            Git.check_unsafe_options(options=multi_options, unsafe_options=cls.unsafe_git_clone_options)

        proc = git.clone(
            multi,
            "--",
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

    @staticmethod
    def check_unsafe_protocols(url: str) -> None:
        unsafe = ("ext::", "fd::")
        if url.startswith(unsafe):
            raise ValueError(f"Unsafe git protocol: {url!r}")

    @staticmethod
    def check_unsafe_options(options: List[str], unsafe_options: List[str]) -> None:
        for option in options:
            for unsafe in unsafe_options:
                if option.startswith(unsafe):
                    raise ValueError(f"Unsafe git option: {option!r}")

    def clone(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
