"""Unit tests for email attachment resolution (no network / no live account).

Covers the remote/HTTP deploy fix: email attachments must be passable inline as
base64 (``attachments_inline``) because the Azure container does not share a
filesystem with the client. Local-path mode (``attachments``) is kept for stdio.
"""

import base64

import pytest

from microsoft_mcp.tools import _resolve_email_attachments


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def test_none_returns_empty():
    assert _resolve_email_attachments(None, None) == []


def test_inline_single():
    payload = b"hello world"
    out = _resolve_email_attachments(
        None, [{"name": "doc.txt", "content_base64": _b64(payload)}]
    )
    assert out == [("doc.txt", payload)]


def test_inline_multiple_preserves_order_and_names():
    out = _resolve_email_attachments(
        None,
        [
            {"name": "a.pdf", "content_base64": _b64(b"AAA")},
            {"name": "b.pdf", "content_base64": _b64(b"BBBB")},
        ],
    )
    assert out == [("a.pdf", b"AAA"), ("b.pdf", b"BBBB")]


def test_local_path(tmp_path):
    f = tmp_path / "report.txt"
    f.write_bytes(b"local bytes")
    out = _resolve_email_attachments(str(f), None)
    assert out == [("report.txt", b"local bytes")]


def test_local_and_inline_combined(tmp_path):
    f = tmp_path / "local.bin"
    f.write_bytes(b"\x00\x01\x02")
    out = _resolve_email_attachments(
        str(f), [{"name": "inline.txt", "content_base64": _b64(b"xyz")}]
    )
    assert out == [("local.bin", b"\x00\x01\x02"), ("inline.txt", b"xyz")]


def test_inline_missing_name_raises():
    with pytest.raises(ValueError, match="name.*content_base64"):
        _resolve_email_attachments(None, [{"content_base64": _b64(b"x")}])


def test_inline_missing_content_raises():
    with pytest.raises(ValueError, match="name.*content_base64"):
        _resolve_email_attachments(None, [{"name": "x.txt"}])


def test_inline_invalid_base64_raises():
    with pytest.raises(ValueError, match="Invalid base64"):
        _resolve_email_attachments(
            None, [{"name": "x.txt", "content_base64": "not!valid!base64"}]
        )
