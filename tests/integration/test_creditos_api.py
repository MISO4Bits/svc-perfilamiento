from __future__ import annotations

import pytest

from app.api.app import create_app
from app.config import Settings

SOLICITUD = {"clienteId": "cli-1", "numeroDocumento": "1000000001"}


async def test_sin_consentimiento_no_hay_hipotecas(client):
    resp = await client.get("/clientes/cli-1/creditos-hipotecarios")

    assert resp.status_code == 200
    assert resp.json() == {
        "estado": "SIN_CONSENTIMIENTO",
        "hipotecas": [],
        "origen": "OPEN_FINANCE",
    }


async def test_con_consentimiento_devuelve_lo_guardado(client):
    await client.post("/perfiles", json=SOLICITUD)

    resp = await client.get("/clientes/cli-1/creditos-hipotecarios")

    cuerpo = resp.json()
    assert cuerpo["estado"] == "DISPONIBLE"
    assert cuerpo["origen"] == "OPEN_FINANCE"
    assert cuerpo["fechaConsulta"]
    assert cuerpo["hipotecas"] == [
        {
            "entidadAcreedora": "BBVA COLOMBIA S.A.",
            "valorCredito": 200000000,
            "saldoInsoluto": 160000000,
            "plazoRestanteMeses": 180,
            "cuotaMensual": 2100000,
        }
    ]


async def test_invalidar_borra_tambien_los_creditos(client):
    await client.post("/perfiles", json=SOLICITUD)
    await client.delete("/perfiles/cli-1")

    resp = await client.get("/clientes/cli-1/creditos-hipotecarios")

    assert resp.json()["estado"] == "SIN_CONSENTIMIENTO"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_lo_guardado_responde_desde_un_pod_reiniciado(tmp_path, backend):
    """Con SQLite el dato sobrevive al reinicio; con memoria no (por eso solo hay SQLite en dev)."""
    settings = Settings(
        adapters="fake", repository_backend=backend, database_path=str(tmp_path / "pod.db")
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await app.state.service.calcular_perfil("cli-1", "1000000001")

    reiniciado = create_app(settings)
    async with reiniciado.router.lifespan_context(reiniciado):
        creditos = await reiniciado.state.service.obtener_creditos_hipotecarios("cli-1")
        perfil_guardado = backend == "sqlite"
        assert (creditos.estado.value == "DISPONIBLE") is perfil_guardado
