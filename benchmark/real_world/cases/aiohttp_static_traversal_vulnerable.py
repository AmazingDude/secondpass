"""Provenance-backed real-world case: aiohttp static file path traversal.

Source repo:      https://github.com/aio-libs/aiohttp
Vulnerable tag:    v3.9.1 (bug present since introduction of follow_symlinks)
File (upstream):   aiohttp/web_urldispatcher.py, StaticResource._handle
License:           Apache-2.0
Advisory:          CVE-2024-23334 / GHSA-5h86-8mv2-jq9f
                    https://github.com/advisories/GHSA-5h86-8mv2-jq9f
Fix commit/PR:      https://github.com/aio-libs/aiohttp/pull/8079

Why the bug is visible in this single file/function alone:
StaticResource is constructed with a `follow_symlinks` flag. When True, the
`_handle` request handler resolves the requested file path and skips the
`filepath.relative_to(self._directory)` containment check entirely (see the
`if not self._follow_symlinks:` guard below) before calling `.resolve()`.
There is no other validation elsewhere in the request path — the missing
containment check on this one branch is the entire vulnerability, so it is
fully legible from this excerpt without any other file.

This excerpt is a verbatim copy of the vulnerable class/method from the
tagged v3.9.1 source (only unrelated methods of the class were trimmed for
brevity; the vulnerable branch is untouched).
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Iterator, Optional

from aiohttp.abc import AbstractRoute
from aiohttp.typedefs import PathLike
from aiohttp.web_exceptions import HTTPForbidden, HTTPNotFound
from aiohttp.web_fileresponse import FileResponse
from aiohttp.web_request import Request
from aiohttp.web_response import Response, StreamResponse
from aiohttp.web_urldispatcher import PrefixResource, ResourceRoute, UrlMappingMatchInfo


class StaticResource(PrefixResource):
    VERSION_KEY = "v"

    def __init__(
        self,
        prefix: str,
        directory: PathLike,
        *,
        name: Optional[str] = None,
        expect_handler=None,
        chunk_size: int = 256 * 1024,
        show_index: bool = False,
        follow_symlinks: bool = False,
        append_version: bool = False,
    ) -> None:
        super().__init__(prefix, name=name)
        directory = Path(directory).resolve()
        self._directory = directory
        self._show_index = show_index
        self._chunk_size = chunk_size
        self._follow_symlinks = follow_symlinks
        self._expect_handler = expect_handler
        self._append_version = append_version

        self._routes = {
            "GET": ResourceRoute("GET", self._handle, self, expect_handler=expect_handler),
            "HEAD": ResourceRoute("HEAD", self._handle, self, expect_handler=expect_handler),
        }

    def __len__(self) -> int:
        return len(self._routes)

    def __iter__(self) -> Iterator[AbstractRoute]:
        return iter(self._routes.values())

    async def _handle(self, request: Request) -> StreamResponse:
        rel_url = request.match_info["filename"]
        try:
            filename = Path(rel_url)
            if filename.anchor:
                # rel_url is an absolute name like
                # /static/\\machine_name\c$ or /static/D:\path
                # where the static dir is totally different
                raise HTTPForbidden()
            filepath = self._directory.joinpath(filename).resolve()
            if not self._follow_symlinks:
                filepath.relative_to(self._directory)
            # BUG: when self._follow_symlinks is True, the containment check
            # above is skipped entirely. `filepath` can point anywhere on the
            # filesystem the process can read (e.g. via ../../ segments or a
            # symlink placed inside the static directory), and it is served
            # unchanged below.
        except (ValueError, FileNotFoundError) as error:
            raise HTTPNotFound() from error
        except HTTPForbidden:
            raise
        except Exception as error:
            request.app.logger.exception(error)
            raise HTTPNotFound() from error

        if filepath.is_dir():
            if self._show_index:
                try:
                    return Response(
                        text=self._directory_as_html(filepath), content_type="text/html"
                    )
                except PermissionError:
                    raise HTTPForbidden()
            else:
                raise HTTPForbidden()
        elif filepath.is_file():
            return FileResponse(filepath, chunk_size=self._chunk_size)
        else:
            raise HTTPNotFound

    def _directory_as_html(self, filepath: Path) -> str:
        assert filepath.is_dir()
        relative_path_to_dir = filepath.relative_to(self._directory).as_posix()
        return f"Index of /{relative_path_to_dir}"
