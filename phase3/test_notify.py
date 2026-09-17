#!/usr/bin/env python3
"""Quick unit checks for Phase 3 eligibility/payload logic (no network required)."""
import json

from notify import build_payload, check_eligibility, event_class_of, topics_for


def row(**overrides):
    base = {
        "id": 1,
        "event": "TOR",
        "event_label": "Tornado Warning",
        "severity": "warning",
        "fips_list": json.dumps(["055025"]),
        "received_at": "2026-09-17T12:00:00+00:00",
        "audio_path": "data/audio/clip.wav",
        "transcript": None,
    }
    base.update(overrides)
    return base


def main() -> None:
    # --- severity gate ---
    warn = row(severity="warning")
    watch = row(severity="watch", event="TOA", event_label="Tornado Watch")
    adv = row(severity="advisory", event="SPS", event_label="Special Weather Statement")

    assert check_eligibility(warn, min_class="warning", notify_tests=False, notify_unknown=False).eligible
    assert not check_eligibility(watch, min_class="warning", notify_tests=False, notify_unknown=False).eligible
    assert not check_eligibility(adv, min_class="warning", notify_tests=False, notify_unknown=False).eligible

    assert check_eligibility(watch, min_class="watch", notify_tests=False, notify_unknown=False).eligible
    assert not check_eligibility(adv, min_class="watch", notify_tests=False, notify_unknown=False).eligible

    assert check_eligibility(adv, min_class="advisory", notify_tests=False, notify_unknown=False).eligible
    assert check_eligibility(adv, min_class="all", notify_tests=False, notify_unknown=False).eligible

    # --- test suppress ---
    test_row = row(severity="test", event="RWT", event_label="Required Weekly Test")
    r_suppressed = check_eligibility(test_row, min_class="warning", notify_tests=False, notify_unknown=False)
    assert not r_suppressed.eligible
    assert r_suppressed.reason == "suppressed:test"
    r_allowed = check_eligibility(test_row, min_class="warning", notify_tests=True, notify_unknown=False)
    assert r_allowed.eligible

    # --- unknown codes ---
    unk_row = row(severity=None, event="ADR", event_label="Administrative Message")
    assert event_class_of(unk_row) == "other"
    r_unk_suppressed = check_eligibility(unk_row, min_class="all", notify_tests=False, notify_unknown=False)
    assert not r_unk_suppressed.eligible
    assert r_unk_suppressed.reason == "suppressed:unknown"
    assert check_eligibility(unk_row, min_class="all", notify_tests=False, notify_unknown=True).eligible

    # --- topics ---
    assert topics_for("warning", topic="house-nwr", urgent_topic="house-urgent") == ["house-nwr", "house-urgent"]
    assert topics_for("watch", topic="house-nwr", urgent_topic="house-urgent") == ["house-nwr"]
    assert topics_for("warning", topic="house-nwr", urgent_topic=None) == ["house-nwr"]

    # --- payload ---
    p = build_payload(warn)
    assert p["title"] == "TOR — Tornado Warning"
    assert "(Dane!)" in p["body"]
    assert "(no transcript yet)" in p["body"]
    assert p["priority"] == 5
    assert "nwr" in p["tags"] and "TOR" in p["tags"] and "warning" in p["tags"]

    no_dane = row(fips_list=json.dumps(["055021"]))
    assert "(Dane!)" not in build_payload(no_dane)["body"]

    long_transcript = row(transcript="x" * 300)
    body = build_payload(long_transcript)["body"]
    assert "…" in body

    print("notify.py OK")


if __name__ == "__main__":
    main()
