"""SAME/EAS header parsing for NWR (Phase 1)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Common NWS SAME event codes -> (label, class). class: test|advisory|watch|warning|other
EVENT_LABELS: dict[str, tuple[str, str]] = {
    "RWT": ("Required Weekly Test", "test"),
    "RMT": ("Required Monthly Test", "test"),
    "NPT": ("National Periodic Test", "test"),
    "DMO": ("Practice/Demo Warning", "test"),
    "ADR": ("Administrative Message", "other"),
    "SVR": ("Severe Thunderstorm Warning", "warning"),
    "SVS": ("Severe Weather Statement", "other"),
    "SVA": ("Severe Thunderstorm Watch", "watch"),
    "TOR": ("Tornado Warning", "warning"),
    "TOA": ("Tornado Watch", "watch"),
    "FFW": ("Flash Flood Warning", "warning"),
    "FFA": ("Flash Flood Watch", "watch"),
    "FLW": ("Flood Warning", "warning"),
    "FLA": ("Flood Watch", "watch"),
    "WSW": ("Winter Storm Warning", "warning"),
    "WSA": ("Winter Storm Watch", "watch"),
    "BZW": ("Blizzard Warning", "warning"),
    "HWA": ("High Wind Watch", "watch"),
    "HWW": ("High Wind Warning", "warning"),
    "SPS": ("Special Weather Statement", "advisory"),
    "SMW": ("Special Marine Warning", "warning"),
    "SQW": ("Snow Squall Warning", "warning"),
    "EWW": ("Extreme Wind Warning", "warning"),
    "CEM": ("Civil Emergency Message", "warning"),
    "LAE": ("Local Area Emergency", "warning"),
    "EVI": ("Evacuation Immediate", "warning"),
    "HMW": ("Hazardous Materials Warning", "warning"),
    "NUW": ("Nuclear Power Plant Warning", "warning"),
    "RHW": ("Radiological Hazard Warning", "warning"),
    "CDW": ("Civil Danger Warning", "warning"),
    "EQW": ("Earthquake Warning", "warning"),
    "FRW": ("Fire Warning", "warning"),
    "HLS": ("Hurricane Local Statement", "other"),
    "HUW": ("Hurricane Warning", "warning"),
    "HUA": ("Hurricane Watch", "watch"),
    "TRW": ("Tropical Storm Warning", "warning"),
    "TRA": ("Tropical Storm Watch", "watch"),
}

_HEADER_RE = re.compile(
    r"ZCZC-"
    r"(?P<org>[A-Z]{3})-"
    r"(?P<event>[A-Z0-9]{3})-"
    r"(?P<body>.+)",
    re.ASCII,
)
_PURGE_SPLIT = re.compile(r"\+(?P<purge>\d{4})-(?P<rest>.+)")


@dataclass
class SameHeader:
    raw: str
    originator: str
    event: str
    event_label: str
    event_class: str
    fips_list: list[str] = field(default_factory=list)
    purge_minutes: int | None = None
    issue_time: str | None = None  # YYMMDDHHMM from header
    station: str | None = None

    @property
    def is_test(self) -> bool:
        return self.event_class == "test" or self.event in {"RWT", "RMT", "NPT", "DMO"}


def _label(event: str) -> tuple[str, str]:
    return EVENT_LABELS.get(event, (event, "other"))


def parse_same_header(line: str) -> SameHeader | None:
    """Parse a multimon-ng SAME/EAS line. Returns None if not a ZCZC header."""
    text = line.strip()
    for prefix in ("EAS:", "SAME:", "EAS ", "SAME "):
        if text.upper().startswith(prefix.upper()):
            text = text[len(prefix) :].strip()
            break
    if "ZCZC-" not in text:
        return None
    text = text[text.index("ZCZC-") :]
    raw = text if text.endswith("-") else text + "-"

    m = _HEADER_RE.match(raw.rstrip("-"))
    if not m:
        return None
    org = m.group("org")
    event = m.group("event")
    body = m.group("body").rstrip("-")

    purge_minutes = None
    issue_time = None
    station = None
    fips_part = body

    pm = _PURGE_SPLIT.search(body)
    if pm:
        fips_part = body[: pm.start()]
        purge_hhmm = pm.group("purge")
        purge_minutes = int(purge_hhmm[:2]) * 60 + int(purge_hhmm[2:])
        rest = pm.group("rest").rstrip("-")
        parts = [p for p in rest.split("-") if p]
        if parts and re.fullmatch(r"\d{10}", parts[0]):
            issue_time = parts[0]
        if parts:
            # Last token is often the 8-char station/call pad
            if re.fullmatch(r"[A-Z0-9]{4,8}", parts[-1]):
                station = parts[-1]

    fips_list = [c for c in fips_part.split("-") if re.fullmatch(r"\d{6}", c or "")]
    label, klass = _label(event)
    return SameHeader(
        raw=raw,
        originator=org,
        event=event,
        event_label=label,
        event_class=klass,
        fips_list=fips_list,
        purge_minutes=purge_minutes,
        issue_time=issue_time,
        station=station,
    )


def is_eom(line: str) -> bool:
    text = line.strip().upper()
    for prefix in ("EAS:", "SAME:", "EAS ", "SAME "):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return "NNNN" in text and "ZCZC" not in text
