#!/usr/bin/env python3
"""Quick unit checks for SAME parsing (no SDR required)."""
from same import is_eom, parse_same_header


def main() -> None:
    h = parse_same_header(
        "EAS: ZCZC-WXR-RWT-055025+0030-2609091200-ABC12345-"
    )
    assert h is not None
    assert h.originator == "WXR"
    assert h.event == "RWT"
    assert h.fips_list == ["055025"]
    assert h.purge_minutes == 30
    assert h.issue_time == "2609091200"
    assert h.is_test

    h2 = parse_same_header(
        "ZCZC-WXR-TOR-055025-055021+0100-2609091830-XYZ98765-"
    )
    assert h2 is not None
    assert h2.event == "TOR"
    assert h2.fips_list == ["055025", "055021"]
    assert h2.purge_minutes == 60
    assert not h2.is_test

    assert is_eom("EAS: NNNN")
    assert parse_same_header("noise") is None
    print("same.py OK")


if __name__ == "__main__":
    main()
