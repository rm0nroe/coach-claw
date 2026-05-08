from __future__ import annotations

import json
from pathlib import Path

import analyze
import redact


def _assistant_record(*, command: str = "") -> dict:
    content = []
    if command:
        content.append({
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": command},
        })
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
    }


def test_analyze_redacts_each_record_before_json_parsing(tmp_path, monkeypatch):
    secret = "sk-" + ("A" * 40)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(_assistant_record(command=f"echo {secret}; pytest")) + "\n"
    )

    real_loads = analyze.json.loads
    parsed_inputs: list[str] = []

    def loads_spy(text, *args, **kwargs):
        parsed_inputs.append(text)
        assert secret not in text
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(analyze.json, "loads", loads_spy)

    sig = analyze.analyze_session(transcript)

    assert sig is not None
    assert sig["test_run_count"] == 1
    assert parsed_inputs
    assert "[REDACTED:openai-key]" in parsed_inputs[0]


def test_analyze_streams_transcript_without_reading_whole_file(tmp_path, monkeypatch):
    transcript = tmp_path / "large.jsonl"
    transcript.write_text(
        "".join(json.dumps(_assistant_record()) + "\n" for _ in range(5000))
    )

    def fail_read_text(*args, **kwargs):
        raise AssertionError("analyze_session must not read whole transcripts")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    sig = analyze.analyze_session(transcript)

    assert sig is not None
    assert sig["assistant_turns"] == 5000


# --- redact.py pattern coverage -------------------------------------------
# Each test passes a bare token in prose context (not a `KEY=value`
# assignment) so we know the token-shape pattern itself catches it, not the
# `.env`-style fallback.


def test_redact_stripe_live_key():
    text = "we use sk_live_" + "a1B2c3D4e5F6g7H8i9J0k1L2" + " for prod"
    out = redact.redact(text)
    assert "sk_live_" not in out
    assert "[REDACTED:stripe-live-key]" in out


def test_redact_stripe_test_key():
    text = "test creds: sk_test_" + ("A" * 30)
    out = redact.redact(text)
    assert "sk_test_" not in out
    assert "[REDACTED:stripe-test-key]" in out


def test_redact_huggingface_token():
    text = "use hf_" + ("a" * 35) + " to download"
    out = redact.redact(text)
    assert "hf_a" not in out
    assert "[REDACTED:huggingface-token]" in out


def test_redact_npm_publish_token():
    text = "npm_" + ("X" * 36) + " is the publish token"
    out = redact.redact(text)
    assert "npm_X" not in out
    assert "[REDACTED:npm-token]" in out


def test_redact_does_not_collapse_short_lookalikes():
    """Don't redact short fragments that happen to start with these
    prefixes — minimum length thresholds matter."""
    text = "sk_live_short hf_short npm_short"
    out = redact.redact(text)
    assert "REDACTED" not in out
