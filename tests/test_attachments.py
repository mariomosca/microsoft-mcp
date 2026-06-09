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


# --- Calendar event attachments (mocked graph, no network) -----------------

from microsoft_mcp import tools  # noqa: E402


def test_list_event_attachments_maps_metadata(monkeypatch):
    captured = {}

    def fake_paginated(path, account_id=None, params=None, limit=None):
        captured["path"] = path
        captured["params"] = params
        yield {
            "id": "att-1",
            "name": "ticket.pdf",
            "contentType": "application/pdf",
            "size": 1234,
            "isInline": False,
        }

    monkeypatch.setattr(tools.graph, "request_paginated", fake_paginated)

    out = tools.list_event_attachments(event_id="evt-1", account_id="acc-1")

    assert captured["path"] == "/me/events/evt-1/attachments"
    assert out == [
        {
            "id": "att-1",
            "name": "ticket.pdf",
            "content_type": "application/pdf",
            "size": 1234,
            "is_inline": False,
        }
    ]


def test_get_event_attachment_inline_base64(monkeypatch):
    payload = b"PNR-ABC123 flight 09:40"
    b64 = base64.b64encode(payload).decode("utf-8")

    def fake_request(method, path, account_id=None, **kwargs):
        assert method == "GET"
        assert path == "/me/events/evt-1/attachments/att-1"
        return {
            "name": "ticket.pdf",
            "contentType": "application/pdf",
            "size": len(payload),
            "contentBytes": b64,
        }

    monkeypatch.setattr(tools.graph, "request", fake_request)

    out = tools.get_event_attachment(
        event_id="evt-1", attachment_id="att-1", account_id="acc-1"
    )

    assert out["name"] == "ticket.pdf"
    assert out["content_type"] == "application/pdf"
    assert out["content_base64"] == b64
    assert "saved_to" not in out


def test_get_event_attachment_save_path(monkeypatch, tmp_path):
    payload = b"binary-bytes"
    b64 = base64.b64encode(payload).decode("utf-8")

    monkeypatch.setattr(
        tools.graph,
        "request",
        lambda *a, **k: {
            "name": "f.bin",
            "contentType": "application/octet-stream",
            "size": len(payload),
            "contentBytes": b64,
        },
    )

    dest = tmp_path / "out" / "f.bin"
    out = tools.get_event_attachment(
        event_id="evt-1",
        attachment_id="att-1",
        account_id="acc-1",
        save_path=str(dest),
    )

    assert dest.read_bytes() == payload
    assert out["saved_to"] == str(dest.resolve())
    assert "content_base64" not in out


def test_get_event_attachment_missing_content_raises(monkeypatch):
    monkeypatch.setattr(
        tools.graph,
        "request",
        lambda *a, **k: {"name": "f", "contentType": "x", "size": 0},
    )
    with pytest.raises(ValueError, match="reference attachment"):
        tools.get_event_attachment(
            event_id="e", attachment_id="a", account_id="acc"
        )


def test_get_event_attachment_large_stages_to_onedrive(monkeypatch):
    # 300 KB payload, default inline cap is 256 KB -> must go to OneDrive.
    payload = b"x" * (300 * 1024)
    b64 = base64.b64encode(payload).decode("utf-8")

    monkeypatch.setattr(
        tools.graph,
        "request",
        lambda *a, **k: {
            "name": "boarding-pass.pdf",
            "contentType": "application/pdf",
            "size": len(payload),
            "contentBytes": b64,
        },
    )

    uploaded = {}

    def fake_upload(path, data, account_id=None, item_properties=None):
        uploaded["path"] = path
        uploaded["bytes"] = len(data)
        return {"id": "drive-item-99", "webUrl": "https://onedrive/boarding-pass.pdf"}

    monkeypatch.setattr(tools.graph, "upload_large_file", fake_upload)

    out = tools.get_event_attachment(
        event_id="evt-1", attachment_id="att-1", account_id="acc-1"
    )

    # bytes were NOT pushed inline
    assert "content_base64" not in out
    assert out["staged_to_onedrive"] is True
    assert out["onedrive_file_id"] == "drive-item-99"
    assert out["web_url"] == "https://onedrive/boarding-pass.pdf"
    assert out["onedrive_path"] == "Attachments/Events/boarding-pass.pdf"
    # the real bytes were uploaded, to the expected drive path
    assert uploaded["bytes"] == len(payload)
    assert uploaded["path"] == "/me/drive/root:/Attachments/Events/boarding-pass.pdf:"


def test_get_event_attachment_large_inline_when_cap_raised(monkeypatch):
    payload = b"y" * (300 * 1024)
    b64 = base64.b64encode(payload).decode("utf-8")
    monkeypatch.setattr(
        tools.graph,
        "request",
        lambda *a, **k: {
            "name": "big.pdf",
            "contentType": "application/pdf",
            "size": len(payload),
            "contentBytes": b64,
        },
    )

    out = tools.get_event_attachment(
        event_id="evt-1",
        attachment_id="att-1",
        account_id="acc-1",
        max_inline_size=1024 * 1024,  # raise cap -> force inline
    )
    assert out["content_base64"] == b64
    assert "staged_to_onedrive" not in out
