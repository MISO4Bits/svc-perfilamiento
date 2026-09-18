from __future__ import annotations

SOLICITUD = {"clienteId": "cli-1", "numeroDocumento": "1000000001"}


async def test_crear_perfil_devuelve_201(client):
    resp = await client.post("/perfiles", json=SOLICITUD)
    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["clienteId"] == "cli-1"
    assert cuerpo["nivelRiesgo"] in ("BAJO", "MEDIO", "ALTO")
    assert "factorAjuste" in cuerpo


async def test_crear_perfil_solicitud_invalida_devuelve_400(client):
    resp = await client.post("/perfiles", json={"clienteId": "cli-1"})
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_obtener_perfil_ya_calculado(client):
    await client.post("/perfiles", json=SOLICITUD)
    resp = await client.get("/perfiles/cli-1")
    assert resp.status_code == 200
    assert resp.json()["clienteId"] == "cli-1"


async def test_obtener_perfil_inexistente_devuelve_404(client):
    resp = await client.get("/perfiles/no-existe")
    assert resp.status_code == 404


async def test_invalidar_perfil_lo_borra(client):
    await client.post("/perfiles", json=SOLICITUD)
    resp = await client.delete("/perfiles/cli-1")
    assert resp.status_code == 204

    resp = await client.get("/perfiles/cli-1")
    assert resp.status_code == 404


async def test_invalidar_perfil_inexistente_tambien_devuelve_204(client):
    resp = await client.delete("/perfiles/no-existe")
    assert resp.status_code == 204


async def test_trae_x_trace_id_deshabilitado_sin_otel(client):
    resp = await client.get("/health")
    assert "X-Trace-Id" not in resp.headers
