"""Puertos de salida del servicio de Perfilamiento.

Nombres del diseño de David (Confluence › "Perfilamiento"). ``ConsentimientoPort``
se retiró del diseño (ver esa página, Sección 8): el propio evento
``ConsentimientoOtorgado`` ya es la prueba de que el consentimiento existe,
no hace falta una llamada síncrona aparte para validarlo. ``CachePort`` queda
fuera de este primer corte — hoy la lectura va directa al repositorio.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import DomainEvent, PerfilRiesgo, SenalOpenData, SenalOpenFinance


@runtime_checkable
class OpenFinancePort(Protocol):
    async def consultar(self, numero_documento: str) -> SenalOpenFinance:
        """Consulta señales financieras del cliente.

        Lanza ``DependenciaNoDisponible`` si la fuente no responde a tiempo
        (timeout 500 ms) o falla — el servicio captura esa excepción y
        continúa con degradación parcial (AC-2 de BITS-106), no bloquea el
        perfilamiento completo.
        """
        ...


@runtime_checkable
class OpenDataPort(Protocol):
    async def consultar(self, numero_documento: str) -> SenalOpenData:
        """Consulta señales sociodemográficas y del inmueble. Misma política
        de degradación parcial que ``OpenFinancePort``."""
        ...


@runtime_checkable
class PerfilRepositoryPort(Protocol):
    async def guardar(self, perfil: PerfilRiesgo) -> None:
        """Persiste (o reemplaza) el perfil vigente de un cliente."""
        ...

    async def obtener(self, cliente_id: str) -> PerfilRiesgo:
        """Recupera el perfil ya calculado. Lanza ``RecursoNoEncontrado`` si
        no existe — nunca dispara un cómputo nuevo (eso lo hace
        ``calcular_perfil``, disparado por el evento de consentimiento)."""
        ...

    async def eliminar(self, cliente_id: str) -> None:
        """Invalida el perfil guardado (disparado por ``ConsentimientoRevocado``).
        Silencioso si no existía."""
        ...


@runtime_checkable
class EventosPort(Protocol):
    async def publicar(self, evento: DomainEvent) -> None:
        """Publica un evento de dominio (``PerfilCalculado``)."""
        ...
