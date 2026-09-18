from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.factory import build_dependencias
from app.adapters.fakes import FakeOpenData, FakeOpenFinance, FakePerfilRepository, LoggingEventos
from app.adapters.opendata_client import OpenDataClientAdapter
from app.adapters.openfinance_client import OpenFinanceClientAdapter
from app.config import Settings
from app.domain import (
    DependenciaNoDisponible,
    DomainEvent,
    NivelRiesgo,
    PerfilRiesgo,
    RecursoNoEncontrado,
)
from app.resilience import ResilientHttpClient, build_breaker

BASE = "http://mock.local"


def _http(path_base: str = "") -> ResilientHttpClient:
    return ResilientHttpClient(
        BASE + path_base,
        breaker=build_breaker("fuente", fail_max=5, reset_timeout=30),
        timeout=0.5,
        retries=0,
    )


# --- fakes ---


async def test_fake_open_finance_disponible_por_defecto():
    senal = await FakeOpenFinance().consultar("1000000001")
    assert senal.nivel_endeudamiento == "MEDIO"


async def test_fake_open_finance_puede_simularse_caido():
    with pytest.raises(DependenciaNoDisponible):
        await FakeOpenFinance(disponible=False).consultar("1000000001")


async def test_fake_open_data_disponible_por_defecto():
    senal = await FakeOpenData().consultar("1000000001")
    assert senal.categoria_salud_actuarial == "ESTANDAR"


async def test_fake_open_data_puede_simularse_caido():
    with pytest.raises(DependenciaNoDisponible):
        await FakeOpenData(disponible=False).consultar("1000000001")


async def test_fake_perfil_repository_guarda_y_obtiene():
    repo = FakePerfilRepository()
    perfil = PerfilRiesgo(
        cliente_id="cli-1", nivel_riesgo=NivelRiesgo.MEDIO, factores=(), factor_ajuste=Decimal("1")
    )
    await repo.guardar(perfil)
    assert (await repo.obtener("cli-1")).cliente_id == "cli-1"


async def test_fake_perfil_repository_lanza_si_no_existe():
    with pytest.raises(RecursoNoEncontrado):
        await FakePerfilRepository().obtener("no-existe")


async def test_fake_perfil_repository_elimina_silenciosamente_si_no_existe():
    await FakePerfilRepository().eliminar("no-existe")  # no debe lanzar


async def test_logging_eventos_no_lanza():
    await LoggingEventos().publicar(DomainEvent("PerfilCalculado", {"clienteId": "cli-1"}))


# --- adaptadores HTTP ---


@respx.mock
async def test_open_finance_client_parsea_la_respuesta():
    respx.post(f"{BASE}/v1/perfil-crediticio").mock(
        return_value=httpx.Response(
            200,
            json={
                "scoreCrediticio": 650,
                "nivelEndeudamiento": "MEDIO",
                "historialPagos": "BUENO",
                "productosActivos": 2,
            },
        )
    )
    adaptador = OpenFinanceClientAdapter(_http())
    try:
        senal = await adaptador.consultar("1000000001")
    finally:
        await adaptador.aclose()
    assert senal.score_crediticio == 650
    assert senal.nivel_endeudamiento == "MEDIO"


@respx.mock
async def test_open_finance_client_propaga_dependencia_no_disponible():
    respx.post(f"{BASE}/v1/perfil-crediticio").mock(return_value=httpx.Response(503))
    adaptador = OpenFinanceClientAdapter(_http())
    try:
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.consultar("9999999999")
    finally:
        await adaptador.aclose()


@respx.mock
async def test_open_data_client_parsea_la_respuesta():
    respx.post(f"{BASE}/v1/perfil-sociodemografico").mock(
        return_value=httpx.Response(
            200,
            json={
                "estrato": 3,
                "ubicacion": {"departamento": "Cundinamarca", "ciudad": "Bogotá"},
                "categoriaSaludActuarial": "ESTANDAR",
            },
        )
    )
    adaptador = OpenDataClientAdapter(_http())
    try:
        senal = await adaptador.consultar("1000000001")
    finally:
        await adaptador.aclose()
    assert senal.estrato == 3
    assert senal.ciudad == "Bogotá"


@respx.mock
async def test_open_data_client_propaga_dependencia_no_disponible():
    respx.post(f"{BASE}/v1/perfil-sociodemografico").mock(return_value=httpx.Response(503))
    adaptador = OpenDataClientAdapter(_http())
    try:
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.consultar("9999999999")
    finally:
        await adaptador.aclose()


# --- fábrica ---


def test_factory_fake_construye_adaptadores_en_memoria():
    deps = build_dependencias(Settings(adapters="fake"))
    assert isinstance(deps.open_finance, FakeOpenFinance)
    assert isinstance(deps.open_data, FakeOpenData)


def test_factory_http_construye_adaptadores_http():
    deps = build_dependencias(Settings(adapters="http"))
    assert isinstance(deps.open_finance, OpenFinanceClientAdapter)
    assert isinstance(deps.open_data, OpenDataClientAdapter)


def test_factory_adapters_no_soportado_lanza():
    with pytest.raises(ValueError):
        build_dependencias(Settings(adapters="ftp"))


async def test_dependencias_aclose_cierra_los_clientes_http():
    deps = build_dependencias(Settings(adapters="http"))
    await deps.aclose()  # no debe lanzar
