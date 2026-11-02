import pytest

from app.api.routers.assets import normalize_hostname
from app.core.errors import ValidationFailed

SHOP = "shop.acme-shipping.example.test"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Shop.Acme-Shipping.example.test", SHOP),
        (SHOP + ".", SHOP),
        ("  api.lumen-retail.example.test ", "api.lumen-retail.example.test"),
        ("bücher.lumen-retail.example.test", "xn--bcher-kva.lumen-retail.example.test"),
        ("10.0.4.12", "10.0.4.12"),
    ],
)
def test_normalize_hostname(raw, expected):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", ".", "https://" + SHOP, "a..example.test", "-bad.example.test", "x" * 64 + ".test"]
)
def test_normalize_hostname_rejects_invalid_names(raw):
    with pytest.raises(ValidationFailed):
        normalize_hostname(raw)


def test_create_stores_the_normalized_hostname(client, acme):
    body = {"hostname": "Shop.Acme-Shipping.example.test.", "kind": "api", "tags": ["Prod"]}
    response = client.post("/v1/assets", headers=acme.headers, json=body)
    assert response.status_code == 201
    asset = response.json()
    assert (asset["hostname"], asset["kind"], asset["tags"]) == (SHOP, "api", ["prod"])


@pytest.mark.parametrize("variant", ["SHOP.acme-shipping.example.test", SHOP + "."])
def test_duplicate_hostname_is_409_per_tenant(client, acme, northpeak, variant):
    first = client.post("/v1/assets", headers=acme.headers, json={"hostname": SHOP})
    duplicate = client.post("/v1/assets", headers=acme.headers, json={"hostname": variant})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["asset_id"] == first.json()["id"]
    other = client.post("/v1/assets", headers=northpeak.headers, json={"hostname": variant})
    assert other.status_code == 201


def test_archived_hostname_can_be_registered_again(client, acme, frozen_clock):
    create = {"hostname": SHOP}
    asset_id = client.post("/v1/assets", headers=acme.headers, json=create).json()["id"]
    assert client.delete(f"/v1/assets/{asset_id}", headers=acme.headers).status_code == 204
    assert client.post("/v1/assets", headers=acme.headers, json=create).status_code == 201

    assert client.get("/v1/assets", headers=acme.headers).json()["total"] == 1
    archived = client.get(f"/v1/assets/{asset_id}", headers=acme.headers).json()
    assert archived["archived_at"] == "2026-10-14T09:00:00Z"
    patch = client.patch(f"/v1/assets/{asset_id}", headers=acme.headers, json={"tags": ["x"]})
    assert patch.status_code == 409


def test_list_paginates_with_bounded_limit(client, acme, factory):
    for _ in range(5):
        factory.asset(acme.tenant)
    page = client.get("/v1/assets", headers=acme.headers, params={"limit": 2, "offset": 2}).json()
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (5, 2, 2, 2)
    assert client.get("/v1/assets", headers=acme.headers, params={"limit": 501}).status_code == 422


def test_other_tenants_assets_are_404(client, acme, northpeak, factory):
    url = f"/v1/assets/{factory.asset(northpeak.tenant).id}"
    assert client.get(url, headers=acme.headers).status_code == 404
    assert client.patch(url, headers=acme.headers, json={"tags": []}).status_code == 404
    assert client.delete(url, headers=acme.headers).status_code == 404
    assert client.get("/v1/assets", headers=acme.headers).json()["total"] == 0
