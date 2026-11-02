import pytest
from sqlalchemy import select

from app.core import auth
from app.core.errors import Unauthorized
from app.models import ApiToken


def test_generated_tokens_have_the_demo_shape():
    tokens = {auth.generate_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        assert token.startswith("tw_demo_")
        assert len(token) == len("tw_demo_") + auth.TOKEN_BODY_LENGTH
        assert auth.looks_like_token(token)


def test_hash_is_salted_sha256_hex():
    token = "tw_demo_" + "a" * 32
    first = auth.hash_token(token, "salt-one")
    second = auth.hash_token(token, "salt-two")
    assert len(first) == 64 and int(first, 16) >= 0
    assert first != second
    assert auth.hash_token(token, "salt-one") == first


def test_verify_accepts_only_the_original_token():
    issued = auth.issue_token()
    assert auth.verify_token(issued.plaintext, issued.salt, issued.token_hash)
    assert not auth.verify_token(issued.plaintext + "x", issued.salt, issued.token_hash)
    assert not auth.verify_token(issued.plaintext, auth.new_salt(), issued.token_hash)
    assert issued.prefix == issued.plaintext[: auth.PREFIX_LENGTH]


def test_issued_token_repr_hides_plaintext():
    issued = auth.issue_token()
    assert issued.plaintext not in repr(issued)
    assert issued.salt not in repr(issued)


def test_create_api_token_stores_only_the_hash(factory, db_session):
    tenant = factory.tenant(slug="acme-shipping")
    row, plaintext = auth.create_api_token(db_session, tenant.id, label="ci")

    stored = db_session.scalars(select(ApiToken).where(ApiToken.id == row.id)).one()
    assert stored.prefix == plaintext[:12]
    assert stored.token_hash != plaintext
    assert plaintext not in (stored.token_hash, stored.salt, stored.label)
    assert auth.verify_token(plaintext, stored.salt, stored.token_hash)
    assert stored.is_active


def test_revoke_sets_timestamp_once(factory, db_session, frozen_clock):
    tenant = factory.tenant()
    row, _ = factory.token(tenant)
    auth.revoke_api_token(db_session, row)
    first = row.revoked_at
    assert first == frozen_clock.now()
    frozen_clock.advance(hours=1)
    auth.revoke_api_token(db_session, row)
    assert row.revoked_at == first
    assert not row.is_active


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic abc", "Bearer", "Bearer   ", "Bearer other_vendor_DEMO_123", "tw_demo_abc"],
)
def test_parse_bearer_rejects_bad_headers(header):
    with pytest.raises(Unauthorized):
        auth.parse_bearer(header)


def test_parse_bearer_extracts_token():
    token = auth.generate_token()
    assert auth.parse_bearer(f"Bearer {token}") == token
    assert auth.parse_bearer(f"bearer   {token}  ") == token


def test_constant_time_equals():
    assert auth.constant_time_equals("tw_demo_admin_x", "tw_demo_admin_x")
    assert not auth.constant_time_equals("tw_demo_admin_x", "tw_demo_admin_y")
