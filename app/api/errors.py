"""Errores en formato RFC 9457 (Problem Details) para el servicio de Perfilamiento."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain import PerfilError
from app.logging_utils import sanear_para_log

logger = logging.getLogger("perfilamiento.api")
PROBLEM_MEDIA_TYPE = "application/problem+json"


def problema(
    status: int,
    title: str,
    *,
    detail: str | None = None,
    instance: str | None = None,
    errores: list[dict] | None = None,
) -> JSONResponse:
    # Punto único de log para todos los handlers de abajo — todos pasan
    # por aquí con su instance=str(request.url), que puede traer \r\n
    # inyectados por el cliente (CWE-117) y hay que sanear antes de
    # escribirlo en un log.
    logger.warning("%s (%s): %s", title, status, sanear_para_log(instance or ""))
    body: dict = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    if errores:
        body["errores"] = errores
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_MEDIA_TYPE)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PerfilError)
    async def _perfil_error(request: Request, exc: PerfilError) -> JSONResponse:
        return problema(exc.status, exc.title, detail=exc.detail, instance=str(request.url))

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError) -> JSONResponse:
        errores = [
            {"campo": ".".join(str(p) for p in err["loc"]), "mensaje": err["msg"]}
            for err in exc.errors()
        ]
        return problema(
            400,
            "Solicitud inválida",
            detail="La solicitud no cumple el esquema",
            instance=str(request.url),
            errores=errores,
        )
