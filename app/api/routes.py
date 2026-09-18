"""Rutas HTTP del servicio de Perfilamiento.

``POST /perfiles`` representa hoy, vía HTTP, lo que en el diseño objetivo
dispara el evento ``ConsentimientoOtorgado`` de CoreTransaccional (ver la
página de componente "Perfilamiento", Sección 8) — no existe todavía un
consumidor real de Pub/Sub. ``GET``/``DELETE`` sí son el contrato final:
lectura barata del perfil ya calculado, e invalidación por
``ConsentimientoRevocado``.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.schemas import PerfilRiesgoOut, SolicitudPerfilIn
from app.logging_utils import sanear_para_log
from app.services import PerfilamientoService

logger = logging.getLogger("perfilamiento.api")
router = APIRouter(tags=["Perfilamiento"])


def get_service(request: Request) -> PerfilamientoService:
    return request.app.state.service


ServiceDep = Annotated[PerfilamientoService, Depends(get_service)]


@router.post(
    "/perfiles",
    response_model=PerfilRiesgoOut,
    status_code=status.HTTP_201_CREATED,
)
async def calcular_perfil(payload: SolicitudPerfilIn, service: ServiceDep) -> PerfilRiesgoOut:
    logger.info(
        "POST /perfiles: solicitud recibida cliente_id=%s", sanear_para_log(payload.cliente_id)
    )
    perfil = await service.calcular_perfil(payload.cliente_id, payload.numero_documento)
    return PerfilRiesgoOut.model_validate(perfil)


@router.get("/perfiles/{cliente_id}", response_model=PerfilRiesgoOut)
async def obtener_perfil(cliente_id: str, service: ServiceDep) -> PerfilRiesgoOut:
    logger.info("GET /perfiles/%s: solicitud recibida", sanear_para_log(cliente_id))
    perfil = await service.obtener_perfil(cliente_id)
    return PerfilRiesgoOut.model_validate(perfil)


@router.delete("/perfiles/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
async def invalidar_perfil(cliente_id: str, service: ServiceDep) -> Response:
    logger.info("DELETE /perfiles/%s: solicitud recibida", sanear_para_log(cliente_id))
    await service.invalidar_perfil(cliente_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
