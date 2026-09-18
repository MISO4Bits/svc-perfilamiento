"""Orquestación y cálculo de perfil de riesgo del servicio de Perfilamiento."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from app.domain import (
    DependenciaNoDisponible,
    DomainEvent,
    EfectoFactor,
    FactorRiesgo,
    NivelRiesgo,
    PerfilRiesgo,
    SenalOpenData,
    SenalOpenFinance,
)
from app.logging_utils import sanear_para_log
from app.ports import EventosPort, OpenDataPort, OpenFinancePort, PerfilRepositoryPort

logger = logging.getLogger("perfilamiento.services")

_FACTOR_BASE = Decimal("1.00")
_FACTOR_MIN = Decimal("0.60")
_FACTOR_MAX = Decimal("1.80")


def calcular_perfil_riesgo(
    cliente_id: str,
    open_finance: SenalOpenFinance | None,
    open_data: SenalOpenData | None,
    fuentes_no_disponibles: tuple[str, ...],
) -> PerfilRiesgo:
    """Combina las señales disponibles en nivel de riesgo + factor de ajuste.

    Heurística de primer corte (no es un modelo actuarial real — caso
    académico): cada señal negativa suma al factor, cada señal positiva
    resta. Con degradación parcial (BITS-106 AC-2): si una fuente no
    respondió, simplemente no aporta factores — nunca bloquea el cálculo.
    """
    factor = _FACTOR_BASE
    factores: list[FactorRiesgo] = []

    if open_finance is not None:
        if open_finance.nivel_endeudamiento == "ALTO":
            factor += Decimal("0.30")
            factores.append(
                FactorRiesgo("Endeudamiento alto", EfectoFactor.NEGATIVO, Decimal("0.4"))
            )
        elif open_finance.nivel_endeudamiento == "BAJO":
            factor -= Decimal("0.10")
            factores.append(
                FactorRiesgo("Endeudamiento bajo", EfectoFactor.POSITIVO, Decimal("0.2"))
            )

        if open_finance.historial_pagos == "IRREGULAR":
            factor += Decimal("0.15")
            factores.append(
                FactorRiesgo("Historial de pagos irregular", EfectoFactor.NEGATIVO, Decimal("0.3"))
            )
        elif open_finance.historial_pagos == "EXCELENTE":
            factor -= Decimal("0.05")

    if open_data is not None:
        if open_data.categoria_salud_actuarial == "ELEVADA":
            factor += Decimal("0.20")
            factores.append(
                FactorRiesgo(
                    "Categoría de salud actuarial elevada", EfectoFactor.NEGATIVO, Decimal("0.3")
                )
            )
        elif open_data.categoria_salud_actuarial == "BAJA":
            factor -= Decimal("0.05")

    factor = min(max(factor, _FACTOR_MIN), _FACTOR_MAX)
    if factor < Decimal("0.95"):
        nivel = NivelRiesgo.BAJO
    elif factor <= Decimal("1.15"):
        nivel = NivelRiesgo.MEDIO
    else:
        nivel = NivelRiesgo.ALTO

    return PerfilRiesgo(
        cliente_id=cliente_id,
        nivel_riesgo=nivel,
        factores=tuple(factores),
        factor_ajuste=factor,
        fuentes_no_disponibles=fuentes_no_disponibles,
    )


class PerfilamientoService:
    def __init__(
        self,
        open_finance: OpenFinancePort,
        open_data: OpenDataPort,
        repositorio: PerfilRepositoryPort,
        eventos: EventosPort,
    ) -> None:
        self._open_finance = open_finance
        self._open_data = open_data
        self._repositorio = repositorio
        self._eventos = eventos

    async def calcular_perfil(self, cliente_id: str, numero_documento: str) -> PerfilRiesgo:
        logger.info(
            "calcular_perfil: consultando fuentes externas en paralelo cliente_id=%s",
            sanear_para_log(cliente_id),
        )

        of_resultado, od_resultado = await asyncio.gather(
            self._open_finance.consultar(numero_documento),
            self._open_data.consultar(numero_documento),
            return_exceptions=True,
        )

        fuentes_no_disponibles: list[str] = []

        open_finance: SenalOpenFinance | None = None
        if isinstance(of_resultado, DependenciaNoDisponible):
            logger.warning("calcular_perfil: open-finance no disponible, degradando parcialmente")
            fuentes_no_disponibles.append("open-finance")
        elif isinstance(of_resultado, BaseException):
            raise of_resultado
        else:
            open_finance = of_resultado

        open_data: SenalOpenData | None = None
        if isinstance(od_resultado, DependenciaNoDisponible):
            logger.warning("calcular_perfil: open-data no disponible, degradando parcialmente")
            fuentes_no_disponibles.append("open-data")
        elif isinstance(od_resultado, BaseException):
            raise od_resultado
        else:
            open_data = od_resultado

        perfil = calcular_perfil_riesgo(
            cliente_id, open_finance, open_data, tuple(fuentes_no_disponibles)
        )
        await self._repositorio.guardar(perfil)
        await self._eventos.publicar(
            DomainEvent(
                "PerfilCalculado",
                {"clienteId": cliente_id, "nivelRiesgo": str(perfil.nivel_riesgo)},
            )
        )
        logger.info(
            "calcular_perfil: perfil calculado cliente_id=%s nivel_riesgo=%s "
            "fuentes_no_disponibles=%s",
            sanear_para_log(cliente_id),
            perfil.nivel_riesgo,
            fuentes_no_disponibles,
        )
        return perfil

    async def obtener_perfil(self, cliente_id: str) -> PerfilRiesgo:
        logger.info("obtener_perfil: consultando cliente_id=%s", sanear_para_log(cliente_id))
        return await self._repositorio.obtener(cliente_id)

    async def invalidar_perfil(self, cliente_id: str) -> None:
        logger.info("invalidar_perfil: cliente_id=%s", sanear_para_log(cliente_id))
        await self._repositorio.eliminar(cliente_id)
