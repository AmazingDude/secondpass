"""Provenance-backed real-world case: aiohttp static file path traversal — FIXED.

Source repo:      https://github.com/aio-libs/aiohttp
Fixed tag:         v3.9.2
File (upstream):   aiohttp/web_urldispatcher.py, StaticResource._handle
License:           Apache-2.0
Advisory:          CVE-2024-23334 / GHSA-5h86-8mv2-jq9f
                    https://github.com/advisories/GHSA-5h86-8mv2-jq9f
Fix commit/PR:      https://github.com/aio-libs/aiohttp/pull/8079

This is the clean/fixed counterpart of aiohttp_static_traversal_vulnerable.py.
The only functional change is that the `follow_symlinks=True` branch now
normalizes the path and re-asserts `relative_to(self._directory)` BEFORE
resolving symlinks, instead of skipping the containment check outright. Used
as a clean control: secondpass should not accept a path_traversal finding on
this file.
"""

from __future__ import annotations

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
                raise HTTPForbidden()
            unresolved_path = self._directory.joinpath(filename)
            if self._follow_symlinks:
                # FIX: normalize and assert containment BEFORE resolving
                # symlinks, so a traversal segment or a symlink escaping the
                # static root is rejected instead of silently followed.
                normalized_path = Path(os.path.normpath(unresolved_path))
                normalized_path.relative_to(self._directory)
                filepath = normalized_path.resolve()
            else:
                filepath = unresolved_path.resolve()
                filepath.relative_to(self._directory)
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
