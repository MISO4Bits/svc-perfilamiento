from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.errors import install_error_handlers, problema
from app.domain import SolicitudInvalida


def test_problema_arma_cuerpo_rfc9457():
    resp = problema(
        404,
        "Recurso no encontrado",
        detail="d",
        instance="http://test/x",
        errores=[{"campo": "a", "mensaje": "b"}],
    )
    assert resp.status_code == 404
    assert resp.media_type == "application/problem+json"


def _mini_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    class Cuerpo(BaseModel):
        n: int

    @app.get("/boom")
    def _boom():
        raise SolicitudInvalida("mal")

    @app.post("/eco")
    def _eco(c: Cuerpo):
        return {"n": c.n}

    return app


async def test_handler_traduce_perfil_error():
    transport = ASGITransport(app=_mini_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 400
    assert resp.json()["title"] == "Solicitud inválida"
    assert resp.json()["detail"] == "mal"


async def test_handler_traduce_error_de_validacion():
    transport = ASGITransport(app=_mini_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/eco", json={"n": "no-es-int"})
    assert resp.status_code == 400
    cuerpo = resp.json()
    assert cuerpo["title"] == "Solicitud inválida"
    assert cuerpo["errores"]
