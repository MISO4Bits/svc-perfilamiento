"""Adaptadores en memoria. Corren el servicio standalone y sirven de dobles en pruebas."""

from __future__ import annotations

import logging
from decimal import Decimal

from app.domain import (
    ConsentimientoFuente,
    ConsultaGuardada,
    DependenciaNoDisponible,
    DomainEvent,
    Fuente,
    Hipoteca,
    PerfilRiesgo,
    RecursoNoEncontrado,
    SenalOpenData,
    SenalOpenFinance,
)

logger = logging.getLogger("perfilamiento.eventos")


class FakeOpenFinance:
    """Fuente simulada. Con ``disponible=False`` imita a Open Finance caído."""

    def __init__(self, *, disponible: bool = True) -> None:
        self._disponible = disponible

    async def consultar(self, numero_documento: str) -> SenalOpenFinance:
        if not self._disponible:
            raise DependenciaNoDisponible("Open Finance no responde")
        return SenalOpenFinance(
            score_crediticio=650,
            nivel_endeudamiento="MEDIO",
            historial_pagos="BUENO",
            productos_activos=2,
            hipotecas=(
                Hipoteca(
                    entidad_acreedora="BBVA COLOMBIA S.A.",
                    valor_credito=Decimal("200000000"),
                    saldo_insoluto=Decimal("160000000"),
                    plazo_restante_meses=180,
                    cuota_mensual=Decimal("2100000"),
                ),
            ),
        )


class FakeOpenData:
    """Fuente simulada. Con ``disponible=False`` imita a Open Data caído."""

    def __init__(self, *, disponible: bool = True) -> None:
        self._disponible = disponible

    async def consultar(self, numero_documento: str) -> SenalOpenData:
        if not self._disponible:
            raise DependenciaNoDisponible("Open Data no responde")
        return SenalOpenData(
            estrato=3,
            departamento="Cundinamarca",
            ciudad="Bogotá",
            categoria_salud_actuarial="ESTANDAR",
        )


class FakePerfilRepository:
    """Guarda perfiles en un dict en memoria."""

    def __init__(self) -> None:
        self._por_cliente: dict[str, PerfilRiesgo] = {}

    async def guardar(self, perfil: PerfilRiesgo) -> None:
        self._por_cliente[perfil.cliente_id] = perfil

    async def obtener(self, cliente_id: str) -> PerfilRiesgo:
        perfil = self._por_cliente.get(cliente_id)
        if perfil is None:
            raise RecursoNoEncontrado(f"perfil de cliente_id={cliente_id} no encontrado")
        return perfil

    async def eliminar(self, cliente_id: str) -> None:
        self._por_cliente.pop(cliente_id, None)


class LoggingEventos:
    async def publicar(self, evento: DomainEvent) -> None:
        # evento.datos puede traer datos sensibles del perfil — no se
        # loguea tal cual, solo el tipo y el id del evento.
        logger.info("evento_dominio: tipo=%s id=%s", evento.tipo, evento.id)


class FakeSenalesRepository:
    """Consentimientos y señales por cliente en memoria."""

    def __init__(self) -> None:
        self._consentimientos: dict[tuple[str, Fuente], ConsentimientoFuente] = {}
        self._senales: dict[tuple[str, Fuente], ConsultaGuardada] = {}

    async def guardar_consentimiento(self, consentimiento: ConsentimientoFuente) -> None:
        self._consentimientos[(consentimiento.cliente_id, consentimiento.fuente)] = consentimiento

    async def obtener_consentimiento(
        self, cliente_id: str, fuente: Fuente
    ) -> ConsentimientoFuente | None:
        return self._consentimientos.get((cliente_id, fuente))

    async def eliminar_consentimiento(self, cliente_id: str, fuente: Fuente) -> None:
        self._consentimientos.pop((cliente_id, fuente), None)

    async def guardar_senal(
        self, cliente_id: str, fuente: Fuente, consulta: ConsultaGuardada
    ) -> None:
        self._senales[(cliente_id, fuente)] = consulta

    async def obtener_senal(self, cliente_id: str, fuente: Fuente) -> ConsultaGuardada | None:
        return self._senales.get((cliente_id, fuente))

    async def eliminar_senal(self, cliente_id: str, fuente: Fuente) -> None:
        self._senales.pop((cliente_id, fuente), None)
