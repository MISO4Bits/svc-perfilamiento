from __future__ import annotations

from decimal import Decimal

import pytest

from app.adapters.fakes import FakePerfilRepository, FakeSenalesRepository, LoggingEventos
from app.domain import (
    DependenciaNoDisponible,
    EfectoFactor,
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


def _of(endeudamiento: str, historial: str) -> SenalOpenFinance:
    return SenalOpenFinance(
        score_crediticio=600,
        nivel_endeudamiento=endeudamiento,
        historial_pagos=historial,
        productos_activos=2,
    )


def _od(salud: str) -> SenalOpenData:
    return SenalOpenData(
        estrato=3, departamento="Cundinamarca", ciudad="Bogotá", categoria_salud_actuarial=salud
    )


# Tabla "Ejemplos" de BITS-106: (endeudamiento, historial, salud o None si Open Data no responde).
@pytest.mark.parametrize(
    ("endeudamiento", "historial", "salud", "factor", "nivel"),
    [
        ("BAJO", "EXCELENTE", "ESTANDAR", "0.85", NivelRiesgo.BAJO),
        ("MEDIO", "BUENO", "ESTANDAR", "1.00", NivelRiesgo.MEDIO),
        ("MEDIO", "IRREGULAR", "ESTANDAR", "1.15", NivelRiesgo.MEDIO),
        ("ALTO", "BUENO", None, "1.30", NivelRiesgo.ALTO),
        ("ALTO", "IRREGULAR", "ELEVADA", "1.65", NivelRiesgo.ALTO),
    ],
)
def test_calcular_perfil_riesgo_ejemplos_de_la_historia(
    endeudamiento, historial, salud, factor, nivel
):
    perfil = calcular_perfil_riesgo(
        "cli-1",
        _of(endeudamiento, historial),
        _od(salud) if salud else None,
        () if salud else ("open-data",),
    )
    assert perfil.factor_ajuste == Decimal(factor)
    assert perfil.nivel_riesgo == nivel


@pytest.mark.parametrize(
    ("endeudamiento", "historial", "salud"),
    [
        (e, h, s)
        for e in ("ALTO", "MEDIO", "BAJO")
        for h in ("IRREGULAR", "BUENO", "EXCELENTE")
        for s in ("ELEVADA", "ESTANDAR", "BAJA")
    ],
)
def test_calcular_perfil_riesgo_cada_ajuste_deja_su_factor(endeudamiento, historial, salud):
    """AC-4: los factores explican todo el precio. Sin límites de por medio, el factor de
    ajuste es 1,00 más los ajustes de los factores guardados (positivo suma, negativo resta)."""
    perfil = calcular_perfil_riesgo("cli-1", _of(endeudamiento, historial), _od(salud), ())
    ajustes = {
        "Endeudamiento alto": Decimal("0.30"),
        "Endeudamiento bajo": Decimal("0.10"),
        "Historial de pagos irregular": Decimal("0.15"),
        "Historial de pagos excelente": Decimal("0.05"),
        "Categoría de salud actuarial elevada": Decimal("0.20"),
        "Categoría de salud actuarial baja": Decimal("0.05"),
    }
    explicado = Decimal("1.00")
    for f in perfil.factores:
        explicado += ajustes[f.descripcion] * (1 if f.efecto is EfectoFactor.NEGATIVO else -1)
    assert explicado == perfil.factor_ajuste


# --- PerfilamientoService (orquestación + degradación parcial) ---


def _servicio(open_finance, open_data) -> PerfilamientoService:
    return PerfilamientoService(
        open_finance,
        open_data,
        FakePerfilRepository(),
        LoggingEventos(),
        FakeSenalesRepository(),
    )


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
