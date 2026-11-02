import io
import json
import logging

import pytest

from app.core.logging import (
    REDACTED,
    JsonFormatter,
    TextFormatter,
    bind,
    configure_logging,
    redact_text,
    redact_value,
)


@pytest.fixture
def capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("tests"))
    logger = logging.getLogger("tests.capture")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.removeHandler(handler)


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_lines_carry_extra_and_bound_fields(capture):
    logger, stream = capture
    with bind(request_id="req-1", tenant_id=3):
        logger.info("scan.started", extra={"scan_id": 42})
    logger.info("outside")
    first, second = _lines(stream)
    assert first["msg"] == "scan.started"
    assert first["service"] == "tests"
    assert first["level"] == "info"
    assert first["ts"].endswith("Z")
    assert (first["request_id"], first["tenant_id"], first["scan_id"]) == ("req-1", 3, 42)
    assert "request_id" not in second


def test_tokens_are_redacted_everywhere(capture):
    logger, stream = capture
    token = "tw_demo_" + "k" * 32
    logger.warning(
        "auth failed for %s",
        token,
        extra={"authorization": f"Bearer {token}", "headers": {"X-Api-Key": "DEMO-123"}},
    )
    line = stream.getvalue()
    assert token not in line
    assert "DEMO-123" not in line
    record = _lines(stream)[0]
    assert record["msg"] == f"auth failed for tw_demo_{REDACTED}"
    assert record["authorization"] == REDACTED
    assert record["headers"] == {"X-Api-Key": REDACTED}


def test_exception_text_is_redacted(capture):
    logger, stream = capture
    try:
        raise ValueError("bad token tw_demo_" + "z" * 32)
    except ValueError:
        logger.exception("boom")
    record = _lines(stream)[0]
    assert "z" * 32 not in record["exc"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password=hunter2 next", f"password={REDACTED} next"),
        ("api_key: DEMO-abc, ok", f"api_key: {REDACTED}, ok"),
        ("Authorization: Bearer abc.def", f"Authorization: Bearer {REDACTED}"),
        ("nothing to see", "nothing to see"),
    ],
)
def test_redact_text(raw, expected):
    assert redact_text(raw) == expected


def test_redact_value_keeps_numbers_under_secret_like_keys():
    assert redact_value("tokens_issued", 3) == 3
    assert redact_value("webhook_secret", "DEMO-whsec") == REDACTED
    assert redact_value("items", ["tw_demo_" + "a" * 32]) == [f"tw_demo_{REDACTED}"]


def test_text_formatter_is_single_line_with_fields():
    record = logging.makeLogRecord(
        {"name": "x", "levelname": "INFO", "msg": "job.claimed", "job_id": 7}
    )
    line = TextFormatter("worker").format(record)
    assert "job.claimed" in line and "job_id=7" in line and "\n" not in line


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging("tests", level="WARNING", fmt="text")
    configure_logging("tests", level="WARNING", fmt="text")
    ours = [h for h in root.handlers if h.get_name() == "tidewright"]
    assert len(ours) == 1
    for handler in ours:
        root.removeHandler(handler)
    for handler in before:
        if handler not in root.handlers:
            root.addHandler(handler)
