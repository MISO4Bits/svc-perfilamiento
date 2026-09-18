from __future__ import annotations

from decimal import Decimal

import pytest

from app.adapters.fakes import FakePerfilRepository, LoggingEventos
from app.domain import (
    DependenciaNoDisponible,
    NivelRiesgo,
    RecursoNoEncontrado,
    SenalOpenData,
    SenalOpenFinance,
)
from app.services import PerfilamientoService, calcular_perfil_riesgo


class _StubOpenFinance:
    def __init__(self, senal: SenalOpenFinance | None = None, *, error: bool = False) -> None:
        self._senal = senal
        self._error = error

    async def consultar(self, numero_documento: str) -> SenalOpenFinance:
        if self._error:
            raise DependenciaNoDisponible("open-finance no responde")
        return self._senal


class _StubOpenData:
    def __init__(self, senal: SenalOpenData | None = None, *, error: bool = False) -> None:
        self._senal = senal
        self._error = error

    async def consultar(self, numero_documento: str) -> SenalOpenData:
        if self._error:
            raise DependenciaNoDisponible("open-data no responde")
        return self._senal


class _StubOpenFinanceRompe:
    async def consultar(self, numero_documento: str) -> SenalOpenFinance:
        raise ValueError("boom inesperado")


SENAL_OF_RIESGOSA = SenalOpenFinance(
    score_crediticio=420,
    nivel_endeudamiento="ALTO",
    historial_pagos="IRREGULAR",
    productos_activos=5,
)
SENAL_OF_SEGURA = SenalOpenFinance(
    score_crediticio=810,
    nivel_endeudamiento="BAJO",
    historial_pagos="EXCELENTE",
    productos_activos=1,
)
SENAL_OD_RIESGOSA = SenalOpenData(
    estrato=2, departamento="Cundinamarca", ciudad="Soacha", categoria_salud_actuarial="ELEVADA"
)
SENAL_OD_SEGURA = SenalOpenData(
    estrato=5, departamento="Cundinamarca", ciudad="Bogotá", categoria_salud_actuarial="ESTANDAR"
)


# --- calcular_perfil_riesgo (función pura) ---


def test_calcular_perfil_riesgo_sin_ninguna_senal_es_riesgo_medio():
    perfil = calcular_perfil_riesgo("cli-1", None, None, ())
    assert perfil.nivel_riesgo == NivelRiesgo.MEDIO
    assert perfil.factor_ajuste == Decimal("1.00")
    assert perfil.factores == ()


def test_calcular_perfil_riesgo_combinacion_riesgosa_da_nivel_alto():
    perfil = calcular_perfil_riesgo("cli-1", SENAL_OF_RIESGOSA, SENAL_OD_RIESGOSA, ())
    assert perfil.nivel_riesgo == NivelRiesgo.ALTO
    assert perfil.factor_ajuste > Decimal("1.15")
    assert len(perfil.factores) == 3  # endeudamiento + historial + salud


def test_calcular_perfil_riesgo_combinacion_segura_da_nivel_bajo():
    perfil = calcular_perfil_riesgo("cli-1", SENAL_OF_SEGURA, SENAL_OD_SEGURA, ())
    assert perfil.nivel_riesgo == NivelRiesgo.BAJO
    assert perfil.factor_ajuste < Decimal("0.95")


def test_calcular_perfil_riesgo_conserva_fuentes_no_disponibles():
    perfil = calcular_perfil_riesgo("cli-1", None, SENAL_OD_SEGURA, ("open-finance",))
    assert perfil.fuentes_no_disponibles == ("open-finance",)


# --- PerfilamientoService (orquestación + degradación parcial) ---


def _servicio(open_finance, open_data) -> PerfilamientoService:
    return PerfilamientoService(open_finance, open_data, FakePerfilRepository(), LoggingEventos())


async def test_calcular_perfil_camino_feliz_ambas_fuentes_disponibles():
    servicio = _servicio(_StubOpenFinance(SENAL_OF_SEGURA), _StubOpenData(SENAL_OD_SEGURA))
    perfil = await servicio.calcular_perfil("cli-1", "1000000002")
    assert perfil.fuentes_no_disponibles == ()
    assert perfil.nivel_riesgo == NivelRiesgo.BAJO


async def test_calcular_perfil_degrada_si_open_finance_no_responde():
    servicio = _servicio(_StubOpenFinance(error=True), _StubOpenData(SENAL_OD_SEGURA))
    perfil = await servicio.calcular_perfil("cli-1", "1000000002")
    assert perfil.fuentes_no_disponibles == ("open-finance",)


async def test_calcular_perfil_degrada_si_open_data_no_responde():
    servicio = _servicio(_StubOpenFinance(SENAL_OF_SEGURA), _StubOpenData(error=True))
    perfil = await servicio.calcular_perfil("cli-1", "1000000002")
    assert perfil.fuentes_no_disponibles == ("open-data",)


async def test_calcular_perfil_con_ambas_fuentes_caidas_igual_produce_un_perfil():
    servicio = _servicio(_StubOpenFinance(error=True), _StubOpenData(error=True))
    perfil = await servicio.calcular_perfil("cli-1", "1000000002")
    assert set(perfil.fuentes_no_disponibles) == {"open-finance", "open-data"}
    assert perfil.nivel_riesgo == NivelRiesgo.MEDIO


async def test_calcular_perfil_propaga_excepcion_inesperada():
    servicio = _servicio(_StubOpenFinanceRompe(), _StubOpenData(SENAL_OD_SEGURA))
    with pytest.raises(ValueError):
        await servicio.calcular_perfil("cli-1", "1000000002")


async def test_calcular_perfil_persiste_y_luego_se_puede_obtener():
    servicio = _servicio(_StubOpenFinance(SENAL_OF_SEGURA), _StubOpenData(SENAL_OD_SEGURA))
    creado = await servicio.calcular_perfil("cli-1", "1000000002")
    obtenido = await servicio.obtener_perfil("cli-1")
    assert obtenido.cliente_id == creado.cliente_id
    assert obtenido.factor_ajuste == creado.factor_ajuste


async def test_invalidar_perfil_lo_borra_del_repositorio():
    servicio = _servicio(_StubOpenFinance(SENAL_OF_SEGURA), _StubOpenData(SENAL_OD_SEGURA))
    await servicio.calcular_perfil("cli-1", "1000000002")
    await servicio.invalidar_perfil("cli-1")
    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_perfil("cli-1")
