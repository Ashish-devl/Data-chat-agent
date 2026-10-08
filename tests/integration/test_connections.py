from tests.integration.conftest import (
    MY_READONLY,
    MY_WRITER,
    PG_ADMIN_URL,
    PG_READONLY,
    PG_WRITER,
    add_connection,
    make_tenant,
    scanned_connection,
)


def test_create_hides_password_and_detects_dialect(client, admin):
    conn = add_connection(client, admin, PG_READONLY)
    assert conn["dialect"] == "postgresql"
    assert conn["status"] == "new"
    assert "erp_readonly@" in conn["target"] and "erp_readonly:" not in conn["target"]
    assert "dsn" not in conn


def test_invalid_dsn_rejected(client, admin):
    for dsn in ("not a url", "sqlite:///x.db", "postgresql://u:p@host"):
        r = client.post("/v1/connections", headers=admin, json={"name": "x", "dsn": dsn})
        assert r.status_code == 422, dsn


def test_list_get_update_delete(client, admin):
    conn = add_connection(client, admin, PG_READONLY)
    ids = [c["id"] for c in client.get("/v1/connections", headers=admin).json()]
    assert conn["id"] in ids
    r = client.patch(f"/v1/connections/{conn['id']}", headers=admin, json={"name": "renamed"})
    assert r.json()["name"] == "renamed"
    assert client.delete(f"/v1/connections/{conn['id']}", headers=admin).status_code == 204
    assert client.get(f"/v1/connections/{conn['id']}", headers=admin).status_code == 404


def test_other_tenant_cannot_see_or_touch_connection(client, admin):
    conn = add_connection(client, admin, PG_READONLY)
    other = make_tenant(client)
    assert conn["id"] not in [c["id"] for c in client.get("/v1/connections", headers=other).json()]
    for method, path in [
        ("get", ""),
        ("patch", ""),
        ("delete", ""),
        ("post", "/test"),
        ("post", "/scan"),
        ("get", "/schema"),
    ]:
        kwargs = {"json": {"name": "x"}} if method == "patch" else {}
        r = getattr(client, method)(f"/v1/connections/{conn['id']}{path}", headers=other, **kwargs)
        assert r.status_code == 404, (method, path)


def test_read_only_user_accepted(client, admin):
    conn = add_connection(client, admin, PG_READONLY)
    r = client.post(f"/v1/connections/{conn['id']}/test", headers=admin).json()
    assert r["status"] == "ok", r["status_detail"]


def test_writer_superuser_and_bad_password_refused(client, admin):
    superuser = PG_ADMIN_URL.rsplit("/", 1)[0] + "/erp_demo"
    bad_password = PG_READONLY.replace("erp_readonly@", "wrong@")
    for dsn, expected in [
        (PG_WRITER, "can write"),
        (superuser, "superuser"),
        (bad_password, "Could not connect"),
    ]:
        conn = add_connection(client, admin, dsn)
        r = client.post(f"/v1/connections/{conn['id']}/test", headers=admin).json()
        assert r["status"] == "rejected" and expected in r["status_detail"], r


def test_scan_refused_for_writer(client, admin):
    conn = add_connection(client, admin, PG_WRITER)
    r = client.post(f"/v1/connections/{conn['id']}/scan", headers=admin)
    assert r.status_code == 409


def test_scan_reads_tables_keys_counts_and_samples(client, admin):
    conn = add_connection(client, admin, PG_READONLY)
    r = client.post(f"/v1/connections/{conn['id']}/scan", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == 8
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    assert set(schema) == {
        "buyers",
        "suppliers",
        "materials",
        "styles",
        "purchase_orders",
        "purchase_order_lines",
        "production_orders",
        "employees",
    }
    assert schema["buyers"]["row_count"] == 6
    cols = {c["name"]: c for c in schema["purchase_order_lines"]["columns"]}
    assert cols["id"]["is_primary_key"]
    assert cols["po_id"]["foreign_key"] == "public.purchase_orders.id"
    status_samples = {c["name"]: c for c in schema["production_orders"]["columns"]}["status"]
    assert set(status_samples["sample_values"]) <= {"Completed", "Delayed", "In Progress"}
    assert 0 < len(status_samples["sample_values"]) <= 5


def test_semantic_layer_hide_and_describe_survive_rescan(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    phone = {c["name"]: c for c in schema["employees"]["columns"]}["phone"]
    assert phone["sample_values"]

    r = client.patch(
        f"/v1/schema-items/{phone['id']}",
        headers=admin,
        json={"hidden": True, "description": "Personal mobile number"},
    )
    assert r.status_code == 200 and r.json()["hidden"] is True

    assert client.post(f"/v1/connections/{conn['id']}/scan", headers=admin).status_code == 200
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    phone = {c["name"]: c for c in schema["employees"]["columns"]}["phone"]
    assert phone["hidden"] is True
    assert phone["description"] == "Personal mobile number"
    assert phone["sample_values"] == []


def test_hiding_a_table_clears_its_samples(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    client.patch(
        f"/v1/schema-items/{schema['employees']['id']}", headers=admin, json={"hidden": True}
    )
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    assert schema["employees"]["hidden"]
    assert all(c["sample_values"] == [] for c in schema["employees"]["columns"])


def test_schema_item_of_other_tenant_is_404(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    item = client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()[0]
    other = make_tenant(client)
    r = client.patch(f"/v1/schema-items/{item['id']}", headers=other, json={"hidden": True})
    assert r.status_code == 404


def test_mysql_read_only_check_and_scan(client, admin, mysql_available):
    writer = add_connection(client, admin, MY_WRITER)
    r = client.post(f"/v1/connections/{writer['id']}/test", headers=admin).json()
    assert r["status"] == "rejected"

    conn = add_connection(client, admin, MY_READONLY)
    assert conn["dialect"] == "mysql"
    r = client.post(f"/v1/connections/{conn['id']}/scan", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == 8
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    assert schema["buyers"]["schema_name"] == "erp_demo"
    cols = {c["name"]: c for c in schema["materials"]["columns"]}
    assert cols["supplier_id"]["foreign_key"] == "erp_demo.suppliers.id"
