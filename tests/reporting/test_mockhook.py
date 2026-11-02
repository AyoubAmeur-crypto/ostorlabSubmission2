import json

from app.reporting.webhooks import sign
from mockhook.server import append_record, handle_delivery

SECRET = "DEMO-whsec-0123456789abcdef"
BODY = b'{"id":1,"event":"finding.new"}'


def test_mockhook_verifies_signature_when_secret_set(tmp_path):
    env = {"MOCKHOOK_SECRET": SECRET}
    headers = {"X-Tidewright-Signature": sign(SECRET, 1792141200, BODY),
               "X-Tidewright-Event": "finding.new"}
    status, record = handle_delivery("/hooks/acme-shipping", headers, BODY, env, 1792141210)
    assert status == 204 and record["signature_ok"] is True
    assert record["signature"] == headers["X-Tidewright-Signature"]
    assert record["tenant_slug"] == "acme-shipping" and record["body"]["id"] == 1
    assert handle_delivery("/hooks/acme-shipping", headers, BODY + b" ", env, 1792141210)[0] == 401
    assert handle_delivery("/hooks/acme-shipping", {}, BODY, {}, 0)[0] == 204
    assert handle_delivery("/other", headers, BODY, env, 0) == (404, None)
    append_record(tmp_path / "log" / "deliveries.jsonl", record)
    assert json.loads((tmp_path / "log" / "deliveries.jsonl").read_text())["event"] == "finding.new"
