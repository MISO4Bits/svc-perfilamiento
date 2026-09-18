"""Despacho de eventos de dominio hacia los casos de uso del servicio.

Aislado de la mecánica de transporte (Pub/Sub, ver
``adapters/pubsub_consumer.py``) a propósito: esta función es pura respecto
del evento ya decodificado, así que se prueba sin credenciales de GCP ni
infraestructura real — solo con un ``DomainEvent`` y un
``PerfilamientoService``.

CoreTransaccional publica ``ConsentimientoOtorgado``/``ConsentimientoRevocado``
al otorgar/revocar consentimiento (ver ``svc-core/app/services.py``). Ver
Confluence, página Perfilamiento, Sección 8 (decisión 2026-09-18): el cálculo
del perfil de riesgo lo dispara el consentimiento, de forma asíncrona — nunca
el llamado síncrono de cotización.
"""

from __future__ import annotations

import logging

from app.domain import DomainEvent
from app.logging_utils import sanear_para_log
from app.services import PerfilamientoService

logger = logging.getLogger("perfilamiento.consumidores")


async def despachar_evento(servicio: PerfilamientoService, evento: DomainEvent) -> None:
    if evento.tipo == "ConsentimientoOtorgado":
        cliente_id = evento.datos["clienteId"]
        numero_documento = evento.datos["numeroDocumento"]
        logger.info(
            "despachar_evento: ConsentimientoOtorgado cliente_id=%s -> calculando perfil",
            sanear_para_log(cliente_id),
        )
        await servicio.calcular_perfil(cliente_id, numero_documento)
        return

    if evento.tipo == "ConsentimientoRevocado":
        cliente_id = evento.datos["clienteId"]
        logger.info(
            "despachar_evento: ConsentimientoRevocado cliente_id=%s -> invalidando perfil",
            sanear_para_log(cliente_id),
        )
        await servicio.invalidar_perfil(cliente_id)
        return

    # Filtro de la suscripción (iac-gcp-dev/modules/pubsub) ya deja pasar
    # solo estos dos tipos — llegar aquí es una señal de que el filtro y
    # este despacho se desalinearon.
    logger.warning("despachar_evento: tipo de evento no reconocido tipo=%s", evento.tipo)
