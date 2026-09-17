"""Notify eligibility, payload shape, and hub HTTP publish (design brief §9, Phase 3).

No cloud SaaS — publishes to ts-notify-hub over Tailscale. Pure stdlib
(`urllib`) since the payload is tiny JSON and Protectli shouldn't need an
extra dependency just for this.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Row

DANE_FIPS = "055025"

# advisory < watch < warning. "test" and "other" are gated by their own
# NOTIFY_TESTS / NOTIFY_UNKNOWN flags, not this ranking.
CLASS_RANK = {"advisory": 1, "watch": 2, "warning": 3}
PRIORITY = {"warning": 5, "watch": 4, "advisory": 3, "test": 2, "other": 1}


class NotifyError(RuntimeError):
    """Publish failed; caller should log, not mark the row notified, and retry later."""


@dataclass
class Eligibility:
    eligible: bool
    reason: str  # "" if eligible; "suppressed:<why>" if not — stored verbatim in notify_topic


def event_class_of(row: Row) -> str:
    """`severity` is already event_class filtered to {warning,watch,advisory,test}
    at insert time (phase1/db.py); anything else (unknown SAME code) is "other"."""
    return row["severity"] or "other"


def check_eligibility(row: Row, *, min_class: str, notify_tests: bool, notify_unknown: bool) -> Eligibility:
    klass = event_class_of(row)

    if klass == "test":
        return Eligibility(notify_tests, "" if notify_tests else "suppressed:test")

    if klass == "other":
        return Eligibility(notify_unknown, "" if notify_unknown else "suppressed:unknown")

    # advisory / watch / warning
    if min_class == "all":
        return Eligibility(True, "")
    required = CLASS_RANK.get(min_class, CLASS_RANK["warning"])
    eligible = CLASS_RANK[klass] >= required
    return Eligibility(eligible, "" if eligible else f"suppressed:min_class<{min_class}")


def topics_for(klass: str, *, topic: str, urgent_topic: str | None) -> list[str]:
    topics = [topic]
    if klass == "warning" and urgent_topic:
        topics.append(urgent_topic)
    return topics


def build_payload(row: Row) -> dict:
    fips_list = json.loads(row["fips_list"]) if row["fips_list"] else []
    dane_flag = " (Dane!)" if DANE_FIPS in fips_list else ""

    transcript = row["transcript"]
    if transcript:
        preview = transcript[:200] + ("…" if len(transcript) > 200 else "")
    else:
        preview = "(no transcript yet)"

    audio_name = Path(row["audio_path"]).name if row["audio_path"] else "(no audio)"
    klass = event_class_of(row)

    title = f"{row['event']} — {row['event_label'] or row['event']}"
    body = "\n".join(
        [
            f"FIPS: {', '.join(fips_list) or '(none)'}{dane_flag}",
            f"received_at={row['received_at']} alert_id={row['id']}",
            f"audio: {audio_name}",
            f"transcript: {preview}",
            "",
            "Secondary NWR archive — not a WEA replacement",
        ]
    )
    return {
        "title": title,
        "body": body,
        "priority": PRIORITY.get(klass, 1),
        "tags": ["nwr", row["event"] or "UNK", klass],
    }


def publish(hub_url: str, token: str, topic: str, payload: dict, *, timeout_s: float = 10.0) -> None:
    url = f"{hub_url.rstrip('/')}/v1/publish/{topic}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status >= 300:
                raise NotifyError(f"hub {url} returned HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        raise NotifyError(f"hub {url} returned HTTP {e.code}: {e.read()[:300]!r}") from e
    except urllib.error.URLError as e:
        raise NotifyError(f"could not reach hub {url}: {e.reason}") from e
    except OSError as e:
        raise NotifyError(f"hub publish failed: {e}") from e
