#!/usr/bin/env python
"""Live smoke test for calendar event attachments (v0.2.9).

Exercises the two new tools against the REAL Microsoft Graph API, end to end:
  - list_event_attachments
  - get_event_attachment

This is NOT a unit test (those live in tests/test_attachments.py with a mocked
graph). It needs a real MSAL-cached auth context.

  LOCAL / STDIO ONLY:
      MICROSOFT_MCP_CLIENT_ID set + a cached account (run ./authenticate.py
      first), then:  uv run python smoke_event_attachments.py

  NOTE — does NOT work via `az containerapp exec` on the Azure deploy.
  In HTTP mode the Graph token is OBO, injected per-request by the connector
  (auth_context.current_graph_token); a standalone exec has no request context
  and therefore no token. For the Azure deploy, verify end-to-end from Claude
  Desktop instead (see DESKTOP-SMOKE-event-attachments.md).

What it does (read-only, no mutations):
  1. Resolve an account (first authenticated one, or ACCOUNT_ID env override).
  2. Scan calendar events (default: 60 days back, 60 ahead) looking for the
     first event that has >=1 attachment, via $expand=attachments.
  3. Call list_event_attachments() on that event and print the metadata.
  4. Call get_event_attachment() on the first attachment WITHOUT save_path,
     assert content_base64 is present and decodes, print size + sha-ish head.

Optional env overrides:
  ACCOUNT_ID   force a specific account id
  EVENT_ID     skip the scan, test this event directly
  DAYS_BACK    default 60
  DAYS_AHEAD   default 60
"""

import base64
import os
import sys

from microsoft_mcp import auth, graph
from microsoft_mcp import tools


def _pick_account() -> str:
    forced = os.environ.get("ACCOUNT_ID")
    if forced:
        return forced
    accounts = auth.list_accounts()
    if not accounts:
        sys.exit("FAIL: no authenticated account. Run ./authenticate.py first.")
    acc = accounts[0]
    # auth.list_accounts() returns msal account dicts; id is under 'username'/'home_account_id'
    acc_id = acc.get("username") or acc.get("home_account_id") or acc.get("account_id")
    if not acc_id:
        sys.exit(f"FAIL: could not resolve account id from {acc!r}")
    print(f"[account] {acc_id}")
    return acc_id


def _find_event_with_attachment(account_id: str) -> str | None:
    forced = os.environ.get("EVENT_ID")
    if forced:
        print(f"[scan] using forced EVENT_ID={forced}")
        return forced

    days_back = int(os.environ.get("DAYS_BACK", "60"))
    days_ahead = int(os.environ.get("DAYS_AHEAD", "60"))
    print(f"[scan] events {days_back}d back .. {days_ahead}d ahead, looking for attachments")

    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = (today - dt.timedelta(days=days_back)).isoformat()
    end = (today + dt.timedelta(days=days_ahead + 1)).isoformat()

    params = {
        "startDateTime": start,
        "endDateTime": end,
        "$orderby": "start/dateTime",
        "$top": 50,
        "$select": "id,subject,hasAttachments,start",
    }
    count = 0
    for ev in graph.request_paginated("/me/calendarView", account_id, params=params):
        count += 1
        if ev.get("hasAttachments"):
            print(f"[scan] HIT: '{ev.get('subject')}' ({ev.get('start', {}).get('dateTime')}) id={ev['id'][:24]}...")
            return ev["id"]
    print(f"[scan] scanned {count} events, none with attachments in window")
    return None


def main() -> int:
    account_id = _pick_account()

    event_id = _find_event_with_attachment(account_id)
    if not event_id:
        print(
            "SKIP: no event with attachments found. Attach a file to a calendar "
            "event in the window (or set EVENT_ID) and re-run."
        )
        return 0

    print("\n[1] list_event_attachments")
    metas = tools.list_event_attachments(event_id=event_id, account_id=account_id)
    if not metas:
        print("FAIL: hasAttachments was true but list returned empty")
        return 1
    for m in metas:
        print(f"    - {m['name']} ({m['content_type']}, {m['size']} bytes, inline={m['is_inline']}) id={m['id'][:24]}...")

    first = metas[0]
    print(f"\n[2] get_event_attachment (inline base64) -> {first['name']}")
    got = tools.get_event_attachment(
        event_id=event_id,
        attachment_id=first["id"],
        account_id=account_id,
    )
    if "content_base64" not in got:
        print(f"FAIL: no content_base64 in response: keys={list(got)}")
        return 1
    raw = base64.b64decode(got["content_base64"])
    print(f"    name={got['name']} type={got['content_type']} decoded_bytes={len(raw)}")
    print(f"    head(16 hex)={raw[:16].hex()}")

    print("\nPASS: event attachment list + download work against live Graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
