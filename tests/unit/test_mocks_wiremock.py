"""Consistencia de los mocks de WireMock (``deploy/apps/wiremock``) con el servicio.

Lee los JSON reales de los escenarios y comprueba que (1) el cuerpo trae los
campos que lee el adaptador, (2) los contadores cuadran con las obligaciones y
(3) cada persona de demo produce el perfil de riesgo que dice su escenario.
El repo de ``deploy`` es aparte: si no está al lado, estas pruebas se omiten.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import NivelRiesgo, SenalOpenData, SenalOpenFinance
from app.services import calcular_perfil_riesgo

_POR_DEFECTO = Path(__file__).resolve().parents[3] / "deploy/apps/wiremock/base/mappings"
MAPPINGS = Path(os.environ.get("WIREMOCK_MAPPINGS_DIR", _POR_DEFECTO))

pytestmark = pytest.mark.skipif(
    not MAPPINGS.is_dir(), reason="no se encontró el repo deploy (mappings de WireMock)"
)

TIPOS_PRODUCTO = {"hipotecario", "tarjeta_credito", "libre_inversion", "vehiculo"}
ESTADOS_MORA = {"al_dia", "mora_30_59", "mora_60_89"}

# documento -> (riesgo esperado, factor de ajuste esperado, hipotecas abiertas)
PERSONAS = {
    "1000000001": (NivelRiesgo.ALTO, Decimal("1.65"), 1),  # David
    "1000000002": (NivelRiesgo.BAJO, Decimal("0.85"), 1),  # Daniel
    "1000000003": (NivelRiesgo.MEDIO, Decimal("1.15"), 1),  # Sofía
    "1000000004": (NivelRiesgo.BAJO, Decimal("0.90"), 0),  # Nicolás
    "1000000005": (NivelRiesgo.MEDIO, Decimal("1.00"), 3),  # Valentina
}


def _cargar(prefijo: str) -> dict[str, dict]:
    """documento (o ``default``) -> jsonBody de cada mapping del prefijo."""
    resultado: dict[str, dict] = {}
    for archivo in sorted(MAPPINGS.glob(f"{prefijo}-*.json")):
        mapping = json.loads(archivo.read_text(encoding="utf-8"))
        cuerpo = mapping["response"].get("jsonBody", {})
        if mapping["response"]["status"] != 200 or not (
            {"obligaciones", "estrato"} & cuerpo.keys()
        ):
            continue
        resultado[cuerpo.get("numeroDocumento", "default")] = cuerpo
    return resultado


OPEN_FINANCE = _cargar("open-finance")
OPEN_DATA = _cargar("open-data")


def _senal_of(c: dict) -> SenalOpenFinance:
    return SenalOpenFinance(
        c["scoreCrediticio"], c["nivelEndeudamiento"], c["historialPagos"], c["productosActivos"]
    )


def _senal_od(c: dict) -> SenalOpenData:
    u = c["ubicacion"]
    return SenalOpenData(c["estrato"], u["departamento"], u["ciudad"], c["categoriaSaludActuarial"])


@pytest.mark.parametrize("documento", PERSONAS)
def test_cada_persona_produce_el_perfil_de_su_escenario(documento):
    nivel, factor, _ = PERSONAS[documento]
    perfil = calcular_perfil_riesgo(
        "c", _senal_of(OPEN_FINANCE[documento]), _senal_od(OPEN_DATA[documento]), ()
    )
    assert perfil.nivel_riesgo == nivel
    assert perfil.factor_ajuste == factor


@pytest.mark.parametrize("documento", PERSONAS)
def test_las_obligaciones_cuadran_con_los_contadores(documento):
    cuerpo = OPEN_FINANCE[documento]
    obligaciones = cuerpo["obligaciones"]
    assert cuerpo["productosActivos"] == sum(o["estado"] == "abierta" for o in obligaciones)
    assert {o["tipoProducto"] for o in obligaciones} <= TIPOS_PRODUCTO
    assert {o["estadoMora"] for o in obligaciones} <= ESTADOS_MORA
    for o in obligaciones:
        # La mora declarada debe corresponder a los días de mora.
        assert (o["diasMora"] == 0) == (o["estadoMora"] == "al_dia"), o["idObligacion"]


@pytest.mark.parametrize("documento", PERSONAS)
def test_hipotecas_abiertas_traen_los_campos_del_formulario(documento):
    hipotecas = [
        o
        for o in OPEN_FINANCE[documento]["obligaciones"]
        if o["tipoProducto"] == "hipotecario" and o["estado"] == "abierta"
    ]
    assert len(hipotecas) == PERSONAS[documento][2]
    for h in hipotecas:
        assert h["valorCredito"] >= h["saldoInsoluto"] > 0
        assert 0 < h["plazoRestanteMeses"] <= h["plazoMeses"]
        assert h["entidadAcreedora"]


def test_no_quedan_nombres_en_ingles_en_las_obligaciones():
    ingles = {"tradelines", "accountType", "creditorName", "currentBalance", "daysPastDue"}
    for archivo in MAPPINGS.glob("open-finance-*.json"):
        assert not any(f'"{clave}"' in archivo.read_text(encoding="utf-8") for clave in ingles)


def test_toda_persona_tiene_mapping_propio_de_open_data():
    assert set(PERSONAS) <= set(OPEN_DATA)
