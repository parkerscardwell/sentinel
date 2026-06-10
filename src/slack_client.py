"""Slack delivery: open a DM to Parker and post. Requires a bot token with
chat:write and im:write scopes, and Parker's Slack user ID."""
from __future__ import annotations
import os
import requests

API = "https://slack.com/api"


def _token():
    return os.environ["SLACK_BOT_TOKEN"]


def _call(method: str, payload: dict) -> dict:
    r = requests.post(f"{API}/{method}", json=payload,
                      headers={"Authorization": f"Bearer {_token()}",
                               "Content-Type": "application/json"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"slack {method}: {data.get('error')}")
    return data


def _channel() -> str:
    user = os.environ["PARKER_SLACK_ID"]
    return _call("conversations.open", {"users": user})["channel"]["id"]


def dm_parker(text: str) -> str:
    """Post a DM; returns the message ts so replies can thread under it."""
    data = _call("chat.postMessage", {"channel": _channel(), "text": text, "mrkdwn": True})
    return data.get("ts", "")


def dm_parker_thread(text: str, thread_ts: str) -> None:
    """Post a reply in the thread under a prior DM."""
    _call("chat.postMessage", {"channel": _channel(), "text": text,
                               "thread_ts": thread_ts, "mrkdwn": True})
