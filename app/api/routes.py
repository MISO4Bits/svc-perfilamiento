"""Rutas HTTP del servicio de Perfilamiento.

``POST /perfiles`` representa, vía HTTP, lo que en el diseño objetivo
disparan los eventos de CoreTransaccional (``ClienteRegistrado`` y
``ConsentimientoOtorgado``, ver la página de componente "Perfilamiento",
Sección 8). ``GET``/``DELETE`` son el contrato final: lectura barata del perfil
ya calculado e invalidación. ``GET /clientes/{id}/creditos-hipotecarios`` sirve
lo que ya se guardó de Open Finance, sin consultar la fuente mientras esté vigente.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.schemas import CreditosHipotecariosOut, PerfilRiesgoOut, SolicitudPerfilIn
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


@router.get(
    "/clientes/{cliente_id}/creditos-hipotecarios",
    response_model=CreditosHipotecariosOut,
    response_model_exclude_none=True,
    tags=["Créditos hipotecarios"],
)
async def obtener_creditos_hipotecarios(
    cliente_id: str, service: ServiceDep
) -> CreditosHipotecariosOut:
    logger.info(
        "GET /clientes/%s/creditos-hipotecarios: solicitud recibida", sanear_para_log(cliente_id)
    )
    creditos = await service.obtener_creditos_hipotecarios(cliente_id)
    return CreditosHipotecariosOut(
        estado=str(creditos.estado),
        hipotecas=[
            {
                "entidad_acreedora": h.entidad_acreedora,
                "valor_credito": h.valor_credito,
                "saldo_insoluto": h.saldo_insoluto,
                "plazo_restante_meses": h.plazo_restante_meses,
                "cuota_mensual": h.cuota_mensual,
            }
            for h in creditos.hipotecas
        ],
        fecha_consulta=creditos.consultado_en,
    )
