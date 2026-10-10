"""Consentimiento por fuente, señales guardadas, revocación selectiva y vigencia de 24 h."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.fakes import FakePerfilRepository, FakeSenalesRepository
from app.domain import (
    DependenciaNoDisponible,
    DomainEvent,
    EstadoCreditos,
    Fuente,
    Hipoteca,
    NivelRiesgo,
    RecursoNoEncontrado,
    SenalOpenData,
    SenalOpenFinance,
)
from app.services import PerfilamientoService

HIPOTECA = Hipoteca(
    entidad_acreedora="DAVIVIENDA S.A.",
    valor_credito=Decimal("220000000"),
    saldo_insoluto=Decimal("178500000"),
    plazo_restante_meses=165,
    cuota_mensual=Decimal("3200000"),
)
OF_CON_HIPOTECA = SenalOpenFinance(612, "ALTO", "IRREGULAR", 3, hipotecas=(HIPOTECA,))
OF_SIN_HIPOTECA = SenalOpenFinance(705, "BAJO", "BUENO", 2)
OD_ELEVADA = SenalOpenData(2, "Cundinamarca", "Soacha", "ELEVADA")


class _Fuente:
    """Fuente con respuesta configurable que cuenta cuántas veces se la consulta."""

    def __init__(self, senal, *, demora: float = 0) -> None:
        self.senal = senal
        self.llamadas = 0
        self.caida = False
        self._demora = demora

    async def consultar(self, numero_documento: str):
        self.llamadas += 1
        if self._demora:
            await asyncio.sleep(self._demora)
        if self.caida:
            raise DependenciaNoDisponible("fuente caída")
        return self.senal


class _Eventos:
    def __init__(self) -> None:
        self.publicados: list[DomainEvent] = []

    async def publicar(self, evento: DomainEvent) -> None:
        self.publicados.append(evento)

    def tipos(self) -> list[str]:
        return [e.tipo for e in self.publicados]


class _Reloj:
    def __init__(self) -> None:
        self.ahora = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, **delta) -> None:
        self.ahora += timedelta(**delta)


@pytest.fixture
def of():
    return _Fuente(OF_CON_HIPOTECA)


@pytest.fixture
def od():
    return _Fuente(OD_ELEVADA)


@pytest.fixture
def eventos():
    return _Eventos()


@pytest.fixture
def reloj():
    return _Reloj()


@pytest.fixture
def servicio(of, od, eventos, reloj):
    return PerfilamientoService(
        of, od, FakePerfilRepository(), eventos, FakeSenalesRepository(), ahora=reloj
    )


# --- ClienteRegistrado / ConsentimientoOtorgado ---


async def test_registro_consulta_solo_las_fuentes_autorizadas(servicio, of, od):
    perfil = await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )

    assert (of.llamadas, od.llamadas) == (1, 0)
    assert perfil.fuentes_no_disponibles == ()  # Open Data sin consentimiento no es "caída"
    assert perfil.nivel_riesgo == NivelRiesgo.ALTO  # endeudamiento ALTO + historial IRREGULAR


async def test_registro_sin_consentimientos_no_consulta_ni_guarda_nada(servicio, of, od, eventos):
    perfil = await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=False, autoriza_open_data=False
    )

    assert perfil is None
    assert (of.llamadas, od.llamadas) == (0, 0)
    assert eventos.publicados == []
    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_perfil("cli-1")


async def test_registro_con_ambas_fuentes_las_consulta_una_vez_cada_una(servicio, of, od):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
    )

    assert (of.llamadas, od.llamadas) == (1, 1)


async def test_otorgar_despues_suma_la_fuente_y_recalcula(servicio, of, od):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    antes = await servicio.obtener_perfil("cli-1")

    await servicio.otorgar_consentimiento("cli-1", "1000000003", Fuente.OPEN_DATA)

    despues = await servicio.obtener_perfil("cli-1")
    assert (of.llamadas, od.llamadas) == (1, 1)  # Open Finance no se reconsulta
    assert despues.factor_ajuste > antes.factor_ajuste  # salud ELEVADA suma


async def test_fuente_caida_al_registrarse_se_degrada_y_queda_pendiente(servicio, of):
    of.caida = True

    perfil = await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )

    assert perfil.fuentes_no_disponibles == ("open-finance",)


async def test_error_inesperado_se_propaga_pero_guarda_lo_que_si_llego(of, od, eventos):
    senales = FakeSenalesRepository()
    servicio = PerfilamientoService(of, od, FakePerfilRepository(), eventos, senales)

    async def _rompe(_):
        raise ValueError("boom")

    of.consultar = _rompe
    with pytest.raises(ValueError):
        await servicio.procesar_registro(
            "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
        )

    assert await senales.obtener_senal("cli-1", Fuente.OPEN_FINANCE) is None
    assert await senales.obtener_senal("cli-1", Fuente.OPEN_DATA) is not None


# --- revocación ---


async def test_revocar_una_fuente_recalcula_con_el_resto_sin_consultar(servicio, of, od, eventos):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
    )
    eventos.publicados.clear()

    await servicio.revocar_consentimiento("cli-1", Fuente.OPEN_DATA)

    assert (of.llamadas, od.llamadas) == (1, 1)  # nadie fue consultado de nuevo
    perfil = await servicio.obtener_perfil("cli-1")
    assert all("salud" not in f.descripcion for f in perfil.factores)  # el dato revocado ya no pesa
    assert eventos.tipos() == ["PerfilCalculado"]


async def test_revocar_open_finance_lo_deja_sin_creditos_y_sin_su_senal(servicio):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
    )

    await servicio.revocar_consentimiento("cli-1", Fuente.OPEN_FINANCE)

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")
    assert creditos.estado is EstadoCreditos.SIN_CONSENTIMIENTO
    assert creditos.hipotecas == ()
    perfil = await servicio.obtener_perfil("cli-1")  # el perfil sigue con Open Data
    assert perfil.fuentes_no_disponibles == ()


async def test_revocar_la_ultima_fuente_borra_el_perfil_y_avisa(servicio, eventos):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    eventos.publicados.clear()

    await servicio.revocar_consentimiento("cli-1", Fuente.OPEN_FINANCE)

    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_perfil("cli-1")
    assert eventos.tipos() == ["PerfilInvalidado"]


async def test_revocar_algo_que_nunca_se_otorgo_es_silencioso(servicio, eventos):
    await servicio.revocar_consentimiento("cli-9", Fuente.OPEN_DATA)

    assert eventos.tipos() == ["PerfilInvalidado"]


# --- créditos hipotecarios ---


async def test_creditos_sin_consentimiento(servicio, of):
    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert creditos.estado is EstadoCreditos.SIN_CONSENTIMIENTO
    assert of.llamadas == 0


async def test_creditos_vigentes_se_sirven_de_lo_guardado_sin_consultar(servicio, of, reloj):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    reloj.avanzar(hours=23, minutes=59)

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert creditos.estado is EstadoCreditos.DISPONIBLE
    assert creditos.hipotecas == (HIPOTECA,)
    assert of.llamadas == 1


async def test_creditos_vencidos_se_reconsultan_una_vez_y_vuelven_a_ser_vigentes(
    servicio, of, reloj
):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    reloj.avanzar(hours=24)

    primero = await servicio.obtener_creditos_hipotecarios("cli-1")
    segundo = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert of.llamadas == 2  # el registro y una sola reconsulta
    assert primero.consultado_en == segundo.consultado_en == reloj.ahora


async def test_creditos_vencidos_con_la_fuente_caida_responden_no_disponible(servicio, of, reloj):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    reloj.avanzar(hours=25)
    of.caida = True

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert creditos.estado is EstadoCreditos.NO_DISPONIBLE
    assert creditos.hipotecas == ()


async def test_creditos_cuyo_registro_fallo_se_recuperan_al_leer(servicio, of):
    of.caida = True
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    of.caida = False

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert creditos.estado is EstadoCreditos.DISPONIBLE
    perfil = await servicio.obtener_perfil("cli-1")
    assert perfil.fuentes_no_disponibles == ()  # el perfil también se recalculó


async def test_cliente_sin_hipotecas(servicio, of):
    of.senal = OF_SIN_HIPOTECA
    await servicio.procesar_registro(
        "cli-1", "1000000004", autoriza_open_finance=True, autoriza_open_data=False
    )

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")

    assert creditos.estado is EstadoCreditos.SIN_HIPOTECAS
    assert creditos.hipotecas == ()
    assert creditos.consultado_en is not None


async def test_dos_lecturas_simultaneas_de_una_senal_vencida_reconsultan_una_sola_vez(
    of, od, eventos, reloj
):
    of.senal = OF_CON_HIPOTECA
    servicio = PerfilamientoService(
        of, od, FakePerfilRepository(), eventos, FakeSenalesRepository(), ahora=reloj
    )
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=False
    )
    reloj.avanzar(hours=30)
    of._demora = 0.05

    a, b = await asyncio.gather(
        servicio.obtener_creditos_hipotecarios("cli-1"),
        servicio.obtener_creditos_hipotecarios("cli-1"),
    )

    assert a.estado is b.estado is EstadoCreditos.DISPONIBLE
    assert of.llamadas == 2  # el registro + una sola reconsulta compartida
    assert servicio._refrescos == {}  # no quedan candados colgados


async def test_el_refresco_tambien_renueva_open_data_vencida(servicio, of, od, reloj):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
    )
    reloj.avanzar(hours=24)

    await servicio.obtener_creditos_hipotecarios("cli-1")

    assert (of.llamadas, od.llamadas) == (2, 2)


async def test_invalidar_perfil_borra_consentimientos_y_senales(servicio):
    await servicio.procesar_registro(
        "cli-1", "1000000003", autoriza_open_finance=True, autoriza_open_data=True
    )

    await servicio.invalidar_perfil("cli-1")

    creditos = await servicio.obtener_creditos_hipotecarios("cli-1")
    assert creditos.estado is EstadoCreditos.SIN_CONSENTIMIENTO
    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener_perfil("cli-1")
