"""Construcción de la app FastAPI del servicio de Perfilamiento."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.adapters.factory import build_dependencias
from app.api.errors import install_error_handlers
from app.api.routes import router
from app.config import Settings, get_settings
from app.logging_utils import SinRuidoDeHealthCheck
from app.services import PerfilamientoService
from app.telemetry import agregar_encabezado_trace_id, setup_telemetry, shutdown_telemetry

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "openapi.yaml"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("uvicorn.access").addFilter(SinRuidoDeHealthCheck())

    deps = build_dependencias(settings)
    service = PerfilamientoService(
        deps.open_finance, deps.open_data, deps.repositorio, deps.eventos
    )

    consumidor = None
    if settings.event_backend == "pubsub":  # pragma: no cover
        from app.adapters.pubsub_consumer import ConsumidorPubSub

        consumidor = ConsumidorPubSub(
            settings.pubsub_project_id or "", settings.pubsub_subscription, service
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if consumidor is not None:
            consumidor.iniciar()
        yield
        if consumidor is not None:
            consumidor.detener()
        await deps.aclose()
        shutdown_telemetry(telemetry)

    app = FastAPI(
        title="svc-perfilamiento — Perfilamiento de clientes",
        version="0.1.0",
        lifespan=lifespan,
    )
    telemetry = setup_telemetry(app, settings)
    agregar_encabezado_trace_id(app)
    app.state.settings = settings
    app.state.deps = deps
    app.state.service = service

    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    if SPEC_PATH.exists():

        @app.get("/openapi.yaml", include_in_schema=False)
        async def openapi_yaml() -> FileResponse:
            return FileResponse(SPEC_PATH, media_type="application/yaml")

    return app
