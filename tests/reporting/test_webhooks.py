import json
from datetime import timedelta

import httpx

from app.core.config import override_settings
from app.reporting import webhooks
from app.reporting.outbox import enqueue_event

SECRET = "DEMO-whsec-0123456789abcdef"
T = 1792141200


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sign_and_verify():
    header = webhooks.sign(SECRET, T, b'{"a":1}')
    assert header.startswith(f"t={T},v1=")
    assert webhooks.verify(SECRET, header, b'{"a":1}', now=T + 60)
    assert webhooks.verify(SECRET, f"{header},v1=deadbeef", b'{"a":1}', now=T)
    assert not webhooks.verify(SECRET, header, b'{"a":2}', now=T)
    assert not webhooks.verify("DEMO-whsec-other000000000", header, b'{"a":1}', now=T)
    assert not webhooks.verify(SECRET, header, b'{"a":1}', now=T + 301)
    assert not webhooks.verify(SECRET, "garbage", b"", now=T)


def test_enqueue_event_only_targets_enabled_subscribed_hooks(factory):
    tenant = factory.tenant()
    factory.webhook(tenant, events=["finding.new"])
    factory.webhook(tenant, events=["scan.completed"])
    factory.webhook(tenant, enabled=False)
    factory.webhook(factory.tenant())
    assert enqueue_event(factory.session, tenant.id, "finding.new", {"finding_id": 1}) == 1
    assert enqueue_event(factory.session, tenant.id, "scan.failed", {}) == 0


def test_dispatch_delivers_signed_envelope(frozen_clock, factory):
    hook = factory.webhook()
    row = factory.outbox(hook, payload={"finding_id": 7})
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(204)

    assert webhooks.dispatch_pending(factory.session, client=_client(handler)) == 1
    req = seen[0]
    now = int(frozen_clock.now().timestamp())
    assert webhooks.verify(SECRET, req.headers["X-Tidewright-Signature"], req.content, now=now)
    assert req.headers["X-Tidewright-Delivery"] == str(row.id)
    assert json.loads(req.content) == {
        "id": row.id, "event": "finding.new", "tenant_id": hook.tenant_id,
        "occurred_at": "2026-10-14T09:00:00Z", "data": {"finding_id": 7},
    }
    assert (row.status, row.attempts, row.delivered_at) == ("delivered", 1, frozen_clock.now())


def test_dispatch_retries_with_backoff_then_gives_up(frozen_clock, factory):
    row = factory.outbox(factory.webhook())
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(503, text="maintenance")

    client = _client(handler)
    with override_settings(webhook_max_attempts=3):
        assert webhooks.dispatch_pending(factory.session, client=client) == 0
        assert (row.status, row.attempts) == ("failed", 1)
        assert row.last_error.startswith("timeout")
        assert row.next_attempt_at == frozen_clock.now() + timedelta(seconds=30)
        webhooks.dispatch_pending(factory.session, client=client)  # not due yet
        assert len(calls) == 1
        frozen_clock.advance(seconds=30)
        webhooks.dispatch_pending(factory.session, client=client)
        assert (row.status, row.attempts, row.last_error) == ("failed", 2, "HTTP 503: maintenance")
        assert row.next_attempt_at == frozen_clock.now() + timedelta(seconds=60)
        frozen_clock.advance(seconds=60)
        webhooks.dispatch_pending(factory.session, client=client)
        assert (row.status, row.attempts) == ("dead", 3)


def test_dispatch_refuses_non_local_hosts_outside_prod(frozen_clock, factory):
    row = factory.outbox(factory.webhook(url="https://203.0.113.10/hook"))
    client = _client(lambda request: httpx.Response(200))
    assert webhooks.dispatch_pending(factory.session, client=client) == 0
    assert row.status == "dead" and row.attempts == 0
