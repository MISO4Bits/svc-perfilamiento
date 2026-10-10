"""El mismo comportamiento de los puertos de persistencia contra memoria y contra SQLite."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.adapters.factory import build_dependencias, build_persistencia
from app.adapters.fakes import FakePerfilRepository, FakeSenalesRepository
from app.adapters.sqlite import SqliteDatabase, SqlitePerfilRepository, SqliteSenalesRepository
from app.config import Settings
from app.domain import (
    ConsentimientoFuente,
    ConsultaGuardada,
    EfectoFactor,
    FactorRiesgo,
    Fuente,
    Hipoteca,
    NivelRiesgo,
    PerfilRiesgo,
    RecursoNoEncontrado,
    SenalOpenData,
    SenalOpenFinance,
)

AHORA = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SENAL_OF = SenalOpenFinance(
    612,
    "MEDIO",
    "IRREGULAR",
    3,
    hipotecas=(
        Hipoteca(
            "DAVIVIENDA S.A.", Decimal("220000000"), Decimal("178500000"), 165, Decimal("3200000")
        ),
        Hipoteca(
            "BBVA COLOMBIA S.A.", Decimal("90000000.50"), Decimal("41000000"), 54, Decimal("980000")
        ),
    ),
)
SENAL_OD = SenalOpenData(4, "Cundinamarca", "Bogotá", "ESTANDAR")
PERFIL = PerfilRiesgo(
    cliente_id="cli-1",
    nivel_riesgo=NivelRiesgo.ALTO,
    factores=(
        FactorRiesgo("Endeudamiento alto", EfectoFactor.NEGATIVO, Decimal("0.4")),
        FactorRiesgo("Sin peso", EfectoFactor.POSITIVO),
    ),
    factor_ajuste=Decimal("1.65"),
    fuentes_no_disponibles=("open-data",),
    calculado_en=AHORA,
)


@pytest_asyncio.fixture(params=["memoria", "sqlite"])
async def repos(request, tmp_path):
    if request.param == "memoria":
        return FakePerfilRepository(), FakeSenalesRepository()
    db = SqliteDatabase(str(tmp_path / "perfilamiento.db"))
    await db.init()
    return SqlitePerfilRepository(db), SqliteSenalesRepository(db)


async def test_consentimiento_se_guarda_se_reemplaza_y_se_borra(repos):
    _, senales = repos
    assert await senales.obtener_consentimiento("cli-1", Fuente.OPEN_FINANCE) is None

    await senales.guardar_consentimiento(ConsentimientoFuente("cli-1", Fuente.OPEN_FINANCE, "111"))
    await senales.guardar_consentimiento(ConsentimientoFuente("cli-1", Fuente.OPEN_FINANCE, "222"))

    guardado = await senales.obtener_consentimiento("cli-1", Fuente.OPEN_FINANCE)
    assert guardado == ConsentimientoFuente("cli-1", Fuente.OPEN_FINANCE, "222")
    assert await senales.obtener_consentimiento("cli-1", Fuente.OPEN_DATA) is None

    await senales.eliminar_consentimiento("cli-1", Fuente.OPEN_FINANCE)
    await senales.eliminar_consentimiento("cli-1", Fuente.OPEN_FINANCE)  # silencioso
    assert await senales.obtener_consentimiento("cli-1", Fuente.OPEN_FINANCE) is None


async def test_senal_de_open_finance_conserva_hipotecas_y_decimales(repos):
    _, senales = repos

    await senales.guardar_senal("cli-1", Fuente.OPEN_FINANCE, ConsultaGuardada(SENAL_OF, AHORA))

    guardada = await senales.obtener_senal("cli-1", Fuente.OPEN_FINANCE)
    assert guardada == ConsultaGuardada(SENAL_OF, AHORA)
    assert guardada.senal.hipotecas[1].valor_credito == Decimal("90000000.50")


async def test_senal_de_open_data_y_aislamiento_entre_fuentes_y_clientes(repos):
    _, senales = repos
    await senales.guardar_senal("cli-1", Fuente.OPEN_DATA, ConsultaGuardada(SENAL_OD, AHORA))

    assert (await senales.obtener_senal("cli-1", Fuente.OPEN_DATA)).senal == SENAL_OD
    assert await senales.obtener_senal("cli-1", Fuente.OPEN_FINANCE) is None
    assert await senales.obtener_senal("cli-2", Fuente.OPEN_DATA) is None


async def test_guardar_una_senal_nueva_reemplaza_la_anterior_y_su_fecha(repos):
    _, senales = repos
    await senales.guardar_senal("cli-1", Fuente.OPEN_DATA, ConsultaGuardada(SENAL_OD, AHORA))
    nueva = SenalOpenData(5, "Antioquia", "Medellín", "BAJA")
    despues = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    await senales.guardar_senal("cli-1", Fuente.OPEN_DATA, ConsultaGuardada(nueva, despues))

    assert await senales.obtener_senal("cli-1", Fuente.OPEN_DATA) == ConsultaGuardada(
        nueva, despues
    )


async def test_eliminar_senal_es_silencioso(repos):
    _, senales = repos
    await senales.guardar_senal("cli-1", Fuente.OPEN_DATA, ConsultaGuardada(SENAL_OD, AHORA))

    await senales.eliminar_senal("cli-1", Fuente.OPEN_DATA)
    await senales.eliminar_senal("cli-1", Fuente.OPEN_DATA)

    assert await senales.obtener_senal("cli-1", Fuente.OPEN_DATA) is None


async def test_perfil_se_guarda_se_reemplaza_y_se_borra(repos):
    perfiles, _ = repos
    await perfiles.guardar(PERFIL)

    assert await perfiles.obtener("cli-1") == PERFIL

    reemplazo = PerfilRiesgo("cli-1", NivelRiesgo.BAJO, (), Decimal("0.85"), (), AHORA)
    await perfiles.guardar(reemplazo)
    assert await perfiles.obtener("cli-1") == reemplazo

    await perfiles.eliminar("cli-1")
    await perfiles.eliminar("cli-1")  # silencioso
    with pytest.raises(RecursoNoEncontrado):
        await perfiles.obtener("cli-1")


async def test_lo_guardado_sobrevive_a_un_reinicio_con_el_mismo_archivo(tmp_path):
    ruta = str(tmp_path / "pod.db")
    db = SqliteDatabase(ruta)
    await db.init()
    await SqliteSenalesRepository(db).guardar_senal(
        "cli-1", Fuente.OPEN_FINANCE, ConsultaGuardada(SENAL_OF, AHORA)
    )

    reinicio = SqliteDatabase(ruta)
    await reinicio.init()  # el esquema es idempotente

    assert (
        await SqliteSenalesRepository(reinicio).obtener_senal("cli-1", Fuente.OPEN_FINANCE)
    ).senal == SENAL_OF


# --- fábrica ---


def test_la_fabrica_elige_el_backend_de_persistencia(tmp_path):
    perfiles, senales, db = build_persistencia(Settings())
    assert isinstance(perfiles, FakePerfilRepository) and db is None

    perfiles, senales, db = build_persistencia(
        Settings(repository_backend="sqlite", database_path=str(tmp_path / "x.db"))
    )
    assert isinstance(perfiles, SqlitePerfilRepository)
    assert isinstance(senales, SqliteSenalesRepository)
    assert isinstance(db, SqliteDatabase)


def test_la_fabrica_rechaza_un_backend_desconocido():
    with pytest.raises(ValueError):
        build_persistencia(Settings(repository_backend="mongo"))


async def test_dependencias_inicializan_el_esquema_solo_con_sqlite(tmp_path):
    await build_dependencias(Settings(adapters="fake")).init()  # memoria: no hace nada

    deps = build_dependencias(
        Settings(adapters="fake", repository_backend="sqlite", database_path=str(tmp_path / "d.db"))
    )
    await deps.init()
    await deps.senales.guardar_consentimiento(ConsentimientoFuente("c", Fuente.OPEN_DATA, "1234"))
    assert await deps.senales.obtener_consentimiento("c", Fuente.OPEN_DATA) is not None
