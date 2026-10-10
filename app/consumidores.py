"""Despacho de eventos de dominio hacia los casos de uso del servicio.

Aislado de la mecánica de transporte (Pub/Sub, ver
``adapters/pubsub_consumer.py``) a propósito: esta función es pura respecto
del evento ya decodificado, así que se prueba sin credenciales de GCP ni
infraestructura real — solo con un ``DomainEvent`` y un
``PerfilamientoService``.

CoreTransaccional publica ``ClienteRegistrado`` al registrarse (con el
consentimiento de cada fuente) y ``ConsentimientoOtorgado``/
``ConsentimientoRevocado`` cuando el cliente cambia un consentimiento después
(ver ``svc-core/app/services.py``). Cada consentimiento es de una sola fuente:
el servicio consulta, guarda o borra únicamente esa. Ver Confluence, página
Perfilamiento, Sección 8 (decisión 2026-09-18): el cálculo del perfil de riesgo
lo dispara el consentimiento, de forma asíncrona — nunca el llamado síncrono de
cotización.
"""

from __future__ import annotations

import logging

from app.domain import DomainEvent, Fuente
from app.logging_utils import sanear_para_log
from app.services import PerfilamientoService

logger = logging.getLogger("perfilamiento.consumidores")


def _fuente_del_scope(scope: str | None) -> Fuente | None:
    try:
        return Fuente(scope)
    except ValueError:
        return None


async def despachar_evento(servicio: PerfilamientoService, evento: DomainEvent) -> None:
    if evento.tipo == "ClienteRegistrado":
        cliente_id = evento.datos["clienteId"]
        logger.info(
            "despachar_evento: ClienteRegistrado cliente_id=%s -> consultando fuentes autorizadas",
            sanear_para_log(cliente_id),
        )
        await servicio.procesar_registro(
            cliente_id,
            evento.datos["numeroDocumento"],
            autoriza_open_finance=bool(evento.datos.get("autorizaDatosFinancieros")),
            autoriza_open_data=bool(evento.datos.get("autorizaTratamientoDatos")),
        )
        return

    if evento.tipo == "ConsentimientoOtorgado":
        cliente_id = evento.datos["clienteId"]
        fuente = _fuente_del_scope(evento.datos.get("scope"))
        if fuente is None:
            logger.warning(
                "despachar_evento: scope no reconocido scope=%s",
                sanear_para_log(str(evento.datos.get("scope"))),
            )
            return
        logger.info(
            "despachar_evento: ConsentimientoOtorgado cliente_id=%s fuente=%s",
            sanear_para_log(cliente_id),
            fuente,
        )
        await servicio.otorgar_consentimiento(cliente_id, evento.datos["numeroDocumento"], fuente)
        return

    if evento.tipo == "ConsentimientoRevocado":
        cliente_id = evento.datos["clienteId"]
        fuente = _fuente_del_scope(evento.datos.get("scope"))
        if fuente is None:
            logger.warning(
                "despachar_evento: scope no reconocido scope=%s",
                sanear_para_log(str(evento.datos.get("scope"))),
            )
            return
        logger.info(
            "despachar_evento: ConsentimientoRevocado cliente_id=%s fuente=%s",
            sanear_para_log(cliente_id),
            fuente,
        )
        await servicio.revocar_consentimiento(cliente_id, fuente)
        return

    # Filtro de la suscripción (iac-gcp-dev/modules/pubsub) ya deja pasar
    # solo estos tres tipos — llegar aquí es una señal de que el filtro y
    # este despacho se desalinearon.
    logger.warning("despachar_evento: tipo de evento no reconocido tipo=%s", evento.tipo)
