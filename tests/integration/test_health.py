from __future__ import annotations


async def test_health_responde_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["status"] == "ok"
    assert cuerpo["service"] == "svc-perfilamiento"
