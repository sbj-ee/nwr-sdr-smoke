#!/usr/bin/env python3
"""Phase 3 notify worker: standalone poller, isolated from phase1/listen.py.

Architecture choice (docs/PHASE3.md, same pattern as phase2/worker.py):
this runs as its own process, polling AlertDB for rows that haven't been
through a notify decision yet (`notified_at IS NULL`), gating them by
severity/test/unknown rules, and POSTing eligible ones to ts-notify-hub
over Tailscale. It never touches the rtl_fm/multimon-ng read loop, so a
hub outage or a stuck HTTP call cannot affect Phase 1 or Phase 2.

Every row in `alerts` is already a first-time insert — Phase 1's dedup
collapses repeat broadcasts within the purge window before a row is ever
created — so there is no separate duplicate check here; `notified_at`
alone prevents re-notifying.

Usage:
    python3 phase3/notify_worker.py                 # poll loop (NOTIFY_ENABLED must be 1)
    python3 phase3/notify_worker.py --once            # single pass, then exit
    python3 phase3/notify_worker.py --once --dry-run  # log what would be sent, no HTTP, no DB writes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phase1"))

from db import AlertDB, utc_now  # phase1/db.py — no package prefix, matches phase1/listen.py's own import style
from notify import NotifyError, build_payload, check_eligibility, event_class_of, publish, topics_for

log = logging.getLogger("nwr.phase3.notify_worker")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def process_row(
    db: AlertDB,
    row,
    *,
    hub_url: str,
    token: str,
    topic: str,
    urgent_topic: str | None,
    min_class: str,
    notify_tests: bool,
    notify_unknown: bool,
    dry_run: bool,
) -> None:
    elig = check_eligibility(row, min_class=min_class, notify_tests=notify_tests, notify_unknown=notify_unknown)

    if not elig.eligible:
        log.info("suppressed alert id=%s event=%s (%s)", row["id"], row["event"], elig.reason)
        if not dry_run:
            db.mark_notified(row["id"], elig.reason)
        return

    klass = event_class_of(row)
    payload = build_payload(row)
    topics = topics_for(klass, topic=topic, urgent_topic=urgent_topic)

    if dry_run:
        log.info("[dry-run] would POST alert id=%s to %s: %s", row["id"], topics, json.dumps(payload))
        return

    sent: list[str] = []
    for i, t in enumerate(topics):
        try:
            publish(hub_url, token, t, payload)
            sent.append(t)
        except NotifyError as e:
            if i == 0:
                # Primary topic failed — leave notified_at null so this row is retried next poll.
                log.error("notify failed for alert id=%s topic=%s: %s", row["id"], t, e)
                return
            # Urgent topic failed but primary already succeeded; don't lose that by retrying primary again.
            log.error("urgent-topic notify failed for alert id=%s topic=%s: %s (primary already sent)", row["id"], t, e)

    db.mark_notified(row["id"], ",".join(sent))
    log.info("notified alert id=%s event=%s topics=%s", row["id"], row["event"], sent)


def poll_once(db: AlertDB, *, limit: int, **kwargs) -> int:
    pending = db.find_pending_notifications(limit=limit)
    for row in pending:
        try:
            process_row(db, row, **kwargs)
        except Exception:
            log.exception("notify decision failed for alert id=%s; leaving unnotified", row["id"])
    return len(pending)


def send_digest(db: AlertDB, since_iso: str, *, hub_url: str, token: str, topic: str, dry_run: bool) -> None:
    counts = db.counts_since(since_iso)
    if not counts:
        return
    total = sum(r["n"] for r in counts)
    lines = [f"{r['n']}x {r['event']} ({r['event_label'] or r['event']})" for r in counts]
    payload = {
        "title": f"NWR digest: {total} new alert(s)",
        "body": "\n".join(lines) + "\n\nSecondary NWR archive — not a WEA replacement",
        "priority": 2,
        "tags": ["nwr", "digest"],
    }
    if dry_run:
        log.info("[dry-run] would POST digest to %s: %s", topic, json.dumps(payload))
        return
    try:
        publish(hub_url, token, topic, payload)
        log.info("sent digest (%d alerts) to %s", total, topic)
    except NotifyError as e:
        log.error("digest publish failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / "config" / "phase1.env")
    load_dotenv(root / "config" / "phase3.env")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", str(root / "data"))))
    p.add_argument("--hub-url", default=os.environ.get("NOTIFY_HUB_URL", ""))
    p.add_argument("--token", default=os.environ.get("NOTIFY_TOKEN", ""))
    p.add_argument("--topic", default=os.environ.get("NOTIFY_TOPIC", "house-nwr"))
    p.add_argument("--urgent-topic", default=os.environ.get("NOTIFY_URGENT_TOPIC", "") or None)
    p.add_argument("--min-class", default=os.environ.get("NOTIFY_MIN_CLASS", "warning"))
    p.add_argument("--notify-tests", action="store_true", default=os.environ.get("NOTIFY_TESTS", "0") == "1")
    p.add_argument("--notify-unknown", action="store_true", default=os.environ.get("NOTIFY_UNKNOWN", "0") == "1")
    p.add_argument("--dry-run", action="store_true", default=os.environ.get("NOTIFY_DRY_RUN", "0") == "1")
    p.add_argument("--interval-s", type=float, default=float(os.environ.get("NOTIFY_POLL_INTERVAL_S", "15")))
    p.add_argument("--limit", type=int, default=int(os.environ.get("NOTIFY_POLL_LIMIT", "20")))
    p.add_argument("--digest", action="store_true", default=os.environ.get("NOTIFY_DIGEST", "0") == "1")
    p.add_argument("--digest-interval-s", type=float, default=float(os.environ.get("NOTIFY_DIGEST_INTERVAL_S", "3600")))
    p.add_argument(
        "--no-immediate",
        dest="immediate",
        action="store_false",
        default=os.environ.get("NOTIFY_IMMEDIATE", "1") == "1",
        help="With --digest: skip the per-row immediate notify pass, rely on digest only",
    )
    p.add_argument("--once", action="store_true", help="Single pass over pending rows, then exit")
    args = p.parse_args(argv)

    kwargs = dict(
        hub_url=args.hub_url,
        token=args.token,
        topic=args.topic,
        urgent_topic=args.urgent_topic,
        min_class=args.min_class,
        notify_tests=args.notify_tests,
        notify_unknown=args.notify_unknown,
        dry_run=args.dry_run,
    )

    if not args.dry_run and not args.hub_url:
        log.error("NOTIFY_HUB_URL is not set (and --dry-run not given) — refusing to start")
        return 1

    if os.environ.get("NOTIFY_ENABLED", "0") != "1" and not args.once:
        log.warning("NOTIFY_ENABLED != 1 — exiting without polling (use --once to force a single pass)")
        return 0

    db = AlertDB(args.data_dir / "alerts.db")
    try:
        if args.once:
            n = poll_once(db, limit=args.limit, **kwargs)
            log.info("processed %d pending row(s)", n)
            return 0

        log.info(
            "phase3 notify worker polling every %.0fs (limit=%d/pass, dry_run=%s, immediate=%s, digest=%s)",
            args.interval_s, args.limit, args.dry_run, args.immediate, args.digest,
        )
        last_digest = time.time()
        last_digest_iso = utc_now()
        while True:
            if args.immediate:
                try:
                    poll_once(db, limit=args.limit, **kwargs)
                except Exception:
                    log.exception("poll pass failed; will retry next interval")

            if args.digest and time.time() - last_digest >= args.digest_interval_s:
                now_iso = utc_now()
                try:
                    send_digest(db, last_digest_iso, hub_url=args.hub_url, token=args.token, topic=args.topic, dry_run=args.dry_run)
                except Exception:
                    log.exception("digest send failed")
                last_digest = time.time()
                last_digest_iso = now_iso

            time.sleep(args.interval_s)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
