from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.schemas import FactorRiesgoOut, PerfilRiesgoOut, SolicitudPerfilIn


def test_solicitud_perfil_acepta_camel_case():
    solicitud = SolicitudPerfilIn.model_validate(
        {"clienteId": "cli-1", "numeroDocumento": "1000000001"}
    )
    assert solicitud.cliente_id == "cli-1"
    assert solicitud.numero_documento == "1000000001"


@pytest.mark.parametrize(
    "numero_documento",
    ["ab", "documento con espacios", "x" * 21],
)
def test_solicitud_perfil_rechaza_documento_invalido(numero_documento):
    with pytest.raises(ValidationError):
        SolicitudPerfilIn.model_validate(
            {"clienteId": "cli-1", "numeroDocumento": numero_documento}
        )


def test_solicitud_perfil_rechaza_campos_extra():
    with pytest.raises(ValidationError):
        SolicitudPerfilIn.model_validate(
            {"clienteId": "cli-1", "numeroDocumento": "1000000001", "otro": "x"}
        )


def test_perfil_riesgo_out_serializa_en_camel_case():
    perfil = PerfilRiesgoOut(
        cliente_id="cli-1",
        nivel_riesgo="ALTO",
        factor_ajuste=1.3,
        factores=[FactorRiesgoOut(descripcion="d", efecto="NEGATIVO", peso_relativo=0.4)],
        fuentes_no_disponibles=[],
        calculado_en=datetime.now(UTC),
    )
    salida = perfil.model_dump(by_alias=True, mode="json")
    assert salida["clienteId"] == "cli-1"
    assert salida["nivelRiesgo"] == "ALTO"
    assert salida["factorAjuste"] == 1.3
    assert salida["factores"][0]["pesoRelativo"] == 0.4
