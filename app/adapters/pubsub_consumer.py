"""Adaptador de producción: consume ``ConsentimientoOtorgado``/
``ConsentimientoRevocado`` desde la suscripción de extracción (pull) que
``iac-gcp-dev/modules/pubsub`` aprovisiona para este servicio, ya filtrada
del lado del servidor a solo estos dos tipos de evento.

El cliente de Pub/Sub (``google-cloud-pubsub``) es síncrono: ``subscribe()``
arranca su propio hilo de fondo que llama a ``_callback`` por cada mensaje.
Como el resto del servicio es ``asyncio``, cada callback se puentea hacia el
loop principal con ``run_coroutine_threadsafe`` — el mismo patrón que
recomienda la documentación de la librería para integrarla con código
asíncrono.

La decisión de qué hacer con cada evento vive en ``app.consumidores`` (se
prueba aparte, sin GCP). Este módulo solo mueve bytes — fuera del alcance de
las pruebas locales (requiere credenciales e infraestructura GCP) y excluido
de la medición de cobertura.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from opentelemetry import propagate, trace

from app.consumidores import despachar_evento
from app.domain import DomainEvent
from app.services import PerfilamientoService

logger = logging.getLogger("perfilamiento.adapters.pubsub_consumer")
tracer = trace.get_tracer("perfilamiento.adapters.pubsub_consumer")


class ConsumidorPubSub:  # pragma: no cover
    def __init__(self, project_id: str, subscription: str, servicio: PerfilamientoService) -> None:
        from google.cloud import pubsub_v1

        self._servicio = servicio
        self._subscriber = pubsub_v1.SubscriberClient()
        self._subscription_path = self._subscriber.subscription_path(project_id, subscription)
        self._future = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def iniciar(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._future = self._subscriber.subscribe(self._subscription_path, callback=self._callback)
        # Si la conexión falla (sin permiso, suscripción inexistente o borrada), la
        # librería solo termina este future: sin este aviso el servicio sigue "vivo" y
        # sin consumir, sin ningún error en los logs.
        self._future.add_done_callback(self._al_terminar)
        logger.info("consumidor_pubsub: escuchando subscription=%s", self._subscription_path)

    def _al_terminar(self, future) -> None:
        if future.cancelled():
            return  # cancelado por detener(): cierre normal
        error = future.exception()
        if error is not None:
            logger.error(
                "consumidor_pubsub: el consumo TERMINÓ con error y ya no se reciben eventos "
                "subscription=%s error=%s: %s",
                self._subscription_path,
                type(error).__name__,
                error,
            )

    def detener(self) -> None:
        if self._future is not None:
            self._future.cancel()
            self._future.result(timeout=10)
        self._subscriber.close()
        logger.info("consumidor_pubsub: detenido")

    def _callback(self, message) -> None:
        # Corre en el hilo de fondo de la librería, no en el loop de
        # asyncio — .result() bloquea ese hilo hasta que _procesar termina
        # (control de flujo: no seguir extrayendo mensajes hasta hacer
        # ack/nack del actual). _procesar nunca deja escapar una excepción
        # (hace nack y retorna), así que esto no la reproduce, solo espera.
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._procesar(message), self._loop).result()

    async def _procesar(self, message) -> None:
        # Extrae el traceparent (W3C) que el publicador inyectó como
        # atributo del mensaje — sin esto, este procesamiento (y todo lo
        # que dispara: llamadas a Open Finance/Open Data, el siguiente
        # publish) queda sin span activo, desconectado del trace original
        # que generó el evento.
        contexto = propagate.extract(dict(message.attributes))
        with tracer.start_as_current_span("perfilamiento.consumir_evento", context=contexto):
            logger.info(
                "consumidor_pubsub: mensaje recibido message_id=%s tipo=%s intento_entrega=%s",
                message.message_id,
                message.attributes.get("tipo", "-"),
                getattr(message, "delivery_attempt", None) or "-",
            )
            try:
                cuerpo = json.loads(message.data.decode("utf-8"))
                evento = DomainEvent(
                    tipo=cuerpo["tipo"],
                    datos=cuerpo["datos"],
                    id=cuerpo["id"],
                    ocurrido_en=datetime.fromisoformat(cuerpo["ocurridoEn"]),
                )
                await despachar_evento(self._servicio, evento)
            except Exception:
                logger.exception(
                    "consumidor_pubsub: fallo procesando message_id=%s", message.message_id
                )
                message.nack()
                return
            message.ack()
