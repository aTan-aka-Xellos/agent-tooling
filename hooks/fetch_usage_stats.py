#!/usr/bin/env python3
"""stop hook: fetch usage stats for the current conversation and log them."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))

HOOK_NAME = Path(__file__).stem
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "log"
_LOG_FILE = _LOG_DIR / "usage_stats.log"

API_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
PAGE_SIZE = 10
REQUEST_DELAY_SEC = 5
REQUIRED_ENV = ("WorkosCursorSessionToken", "CursorTeamId", "CursorUserId")


def _rotated_log_name(default_name: str) -> str:
    # Default rotated name is "<...>/usage_stats.log.12072026";
    # rename to "<...>/usage_stats_12072026.log" to keep the .log extension.
    base, _, date_suffix = default_name.rpartition(".")
    return f"{base.removesuffix('.log')}_{date_suffix}.log"


def get_hook_logger(hook_name: str) -> logging.Logger:
    """Return a logger that writes to <repo>/log/usage_stats.log, tagged with hook_name.

    Rotates daily: on the first run of a new day, the previous file is renamed
    to usage_stats_<DDMMYYYY>.log and a fresh usage_stats.log is started.
    """
    logger = logging.getLogger(f"hooks.{hook_name}")
    if not getattr(logger, "_hook_configured", False):
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            _LOG_FILE, when="midnight", encoding="utf-8"
        )
        handler.suffix = "%d%m%Y"
        handler.namer = _rotated_log_name
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._hook_configured = True  # type: ignore[attr-defined]
    return logger


log = get_hook_logger(HOOK_NAME)


def _read_stdin_bytes() -> bytes:
    try:
        return sys.stdin.buffer.read()
    except (AttributeError, OSError):
        return sys.stdin.read().encode("utf-8")


def read_stdin() -> dict[str, Any]:
    raw_bytes = _read_stdin_bytes()
    if not raw_bytes:
        return {}
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw_bytes.decode("utf-16")
    else:
        text = raw_bytes.decode("utf-8-sig")
    text = text.replace("\x00", "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("invalid JSON on stdin: %s", exc)
        return {}


def date_window() -> tuple[str, str]:
    now_ms = int(time.time() * 1000)
    # Window starts at '-1h' from now; turn's start time is not exposed 
    start_date = str(now_ms - 1 * 60 * 60 * 1000)
    # Window ends at '+12' hours from now
    end_date = str(now_ms + 12 * 60 * 60 * 1000)
    return start_date, end_date


def validate_env() -> tuple[str, int, int] | str:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        return f"missing environment variable(s): {', '.join(missing)}"

    try:
        team_id = int(os.environ["CursorTeamId"])
        user_id = int(os.environ["CursorUserId"])
    except ValueError:
        return "CursorTeamId and CursorUserId must be integers"

    return os.environ["WorkosCursorSessionToken"], team_id, user_id


def _perform_usage_request(request: urllib.request.Request) -> tuple[dict[str, Any], int, str]:
    with urllib.request.urlopen(request, timeout=25) as response:
        status = response.status
        raw = response.read().decode("utf-8")
    data = json.loads(raw) if raw else {}
    return data, status, raw


def fetch_usage_page(
    token: str,
    team_id: int,
    user_id: int,
    start_date: str,
    end_date: str,
    page: int,
) -> dict[str, Any]:
    body_obj = {
        "startDate": start_date,
        "endDate": end_date,
        "teamId": team_id,
        "userId": user_id,
        "page": page,
        "pageSize": PAGE_SIZE,
    }
    body = json.dumps(body_obj).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://cursor.com",
            "Cookie": f"WorkosCursorSessionToken={token}",
        },
    )

    time.sleep(REQUEST_DELAY_SEC)
    data, status, raw = _perform_usage_request(request)

    if not (data.get("usageEventsDisplay") or []):
        log.info("empty response: status=%s body=%s", status, raw)
        log.info("retrying after %ds delay", REQUEST_DELAY_SEC)

        time.sleep(REQUEST_DELAY_SEC)
        data, status, raw = _perform_usage_request(request)

        if not (data.get("usageEventsDisplay") or []):
            log.info("empty response after retry: status=%s body=%s", status, raw)
    return data


def find_latest_matching_event(
    events: list[dict[str, Any]],
    conversation_id: str,
) -> dict[str, Any] | None:
    matches = [e for e in events if str(e.get("conversationId", "")) == conversation_id]
    if not matches:
        return None
    return max(matches, key=lambda e: int(e.get("timestamp", 0)))


def format_utc(timestamp_ms: Any) -> str | None:
    try:
        ts = int(timestamp_ms)
    except (TypeError, ValueError):
        return None
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%b %d, %I:%M:%S %p (UTC)")


def format_jst(timestamp_ms: Any) -> str | None:
    try:
        ts = int(timestamp_ms)
    except (TypeError, ValueError):
        return None
    dt = datetime.fromtimestamp(ts / 1000, tz=JST)
    return dt.strftime("%b %d, %I:%M:%S %p (JST)")


def build_stats_object(event: dict[str, Any]) -> dict[str, Any]:
    token_usage = event.get("tokenUsage") or {}
    timestamp = event.get("timestamp")
    return {
        "conversationId": event.get("conversationId"),
        "timestamp": timestamp,
        "timestampUtc": format_utc(timestamp),
        "timestampJst": format_jst(timestamp),
        "model": event.get("model"),
        "cursorTokenFee": event.get("cursorTokenFee"),
        "requestsCosts": event.get("requestsCosts"),
        "chargedCents": event.get("chargedCents"),
        "tokenUsage": {
            "inputTokens": token_usage.get("inputTokens"),
            "outputTokens": token_usage.get("outputTokens"),
            "cacheReadTokens": token_usage.get("cacheReadTokens"),
            "totalCents": token_usage.get("totalCents"),
        },
    }


def find_event_for_conversation(
    token: str,
    team_id: int,
    user_id: int,
    conversation_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any] | None:
    data = fetch_usage_page(token, team_id, user_id, start_date, end_date, page=1)
    events = data.get("usageEventsDisplay") or []
    match = find_latest_matching_event(events, conversation_id)
    if match is None:
        returned_ids = [str(e.get("conversationId", "")) for e in events]
        log.info(
            "no match: events=%d totalCount=%s pageSize=%d returnedConversationIds=%s",
            len(events),
            data.get("totalUsageEventsCount"),
            PAGE_SIZE,
            json.dumps(returned_ids),
        )
    return match


def main() -> int:
    payload = read_stdin()
    conversation_id = payload.get("conversation_id")

    if not conversation_id:
        log.error("conversation_id not available")
        return 0

    conversation_id = str(conversation_id)

    env_result = validate_env()
    if isinstance(env_result, str):
        log.error(env_result)
        return 0

    token, team_id, user_id = env_result
    start_date, end_date = date_window()

    try:
        event = find_event_for_conversation(
            token, team_id, user_id, conversation_id, start_date, end_date
        )
    except urllib.error.HTTPError as exc:
        log.error("HTTP %s from usage API", exc.code)
        return 0
    except urllib.error.URLError as exc:
        log.error("network error: %s", exc.reason)
        return 0
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.error("failed to parse API response: %s", exc)
        return 0

    if event is None:
        log.error("no usage event found for conversation %s", conversation_id)
        return 0

    stats = build_stats_object(event)
    body = json.dumps(stats, indent=2)
    log.info("startDate:%s endDate:%s stats:\n%s", start_date, end_date, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
