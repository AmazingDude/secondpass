"""Unit tests for Semgrep failure message hygiene (no live Semgrep)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scanner import ScanError, format_semgrep_failure_message, run_static_scan


def test_format_network_dns_failure_is_short_clean_message() -> None:
    noisy = """
Traceback (most recent call last):
  File ".../semgrep/commands/scan.py", line 1, in <module>
    ...
requests.exceptions.ConnectionError: HTTPSConnectionPool(host='semgrep.dev', port=443):
Max retries exceeded with url: /c/p/python
(Caused by NewConnectionError('<urllib3.connection.HTTPSConnection object>:
Failed to establish a new connection: [Errno 11001] getaddrinfo failed'))
"""
    message = format_semgrep_failure_message(stderr=noisy)
    assert message == (
        "Semgrep scan failed: network error, falling back to logic-review"
    )
    assert "Traceback" not in message
    assert "getaddrinfo" not in message


def test_run_static_scan_network_failure_raises_clean_scan_error(monkeypatch) -> None:
    noisy_stderr = (
        "HTTPSConnectionPool(host='semgrep.dev', port=443): "
        "Max retries exceeded (Caused by getaddrinfo failed)"
    )

    monkeypatch.setattr(
        "app.scanner._resolve_semgrep",
        lambda: "semgrep",
    )
    monkeypatch.setattr(
        "app.scanner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stderr=noisy_stderr,
            stdout="",
        ),
    )

    with pytest.raises(ScanError) as exc_info:
        run_static_scan(["some/file.py"])

    assert str(exc_info.value) == (
        "Semgrep scan failed: network error, falling back to logic-review"
    )
    assert "Traceback" not in str(exc_info.value)
