"""Cubre el ciclo de vida (startup/shutdown) de la app — antes nunca se
ejercía porque ningún test dispara los eventos de lifespan de ASGI (el
``client`` de conftest.py usa ``ASGITransport`` directo, sin lifespan)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app


def test_lifespan_arranca_y_detiene_limpiamente(settings):
    app = create_app(settings)
    with TestClient(app):
        pass
