#!/usr/bin/env python3
"""Quick unit checks for whisper-cli JSON parsing (no binary/model required)."""
from stt_whisper_cpp import _parse_result


def main() -> None:
    data = {
        "transcription": [
            {
                "text": " This is a test.",
                "tokens": [
                    {"text": " This", "p": 0.9},
                    {"text": " is", "p": 0.8},
                    {"text": " a", "p": 0.95},
                    {"text": " test.", "p": 0.7},
                ],
            },
            {
                "text": " Second segment.",
                "tokens": [
                    {"text": " Second", "p": 0.6},
                    {"text": " segment.", "p": 0.5},
                ],
            },
        ]
    }
    result = _parse_result(data)
    assert result.text == "This is a test. Second segment.", result.text
    assert abs(result.confidence - (0.9 + 0.8 + 0.95 + 0.7 + 0.6 + 0.5) / 6) < 1e-9

    empty = _parse_result({"transcription": []})
    assert empty.text == ""
    assert empty.confidence is None

    no_tokens = _parse_result({"transcription": [{"text": "hi", "tokens": []}]})
    assert no_tokens.text == "hi"
    assert no_tokens.confidence is None

    print("stt_whisper_cpp.py OK")


if __name__ == "__main__":
    main()
