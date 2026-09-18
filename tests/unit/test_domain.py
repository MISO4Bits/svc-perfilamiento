from __future__ import annotations

from decimal import Decimal

from app.domain import (
    DependenciaNoDisponible,
    DomainEvent,
    EfectoFactor,
    FactorRiesgo,
    NivelRiesgo,
    PerfilError,
    PerfilRiesgo,
    RecursoNoEncontrado,
    SolicitudInvalida,
)


def test_perfil_error_status_y_title_por_defecto():
    exc = PerfilError()
    assert exc.status == 500
    assert exc.title == "Error interno"
    assert exc.detail is None


def test_perfil_error_subclases_tienen_su_propio_status():
    assert SolicitudInvalida().status == 400
    assert RecursoNoEncontrado().status == 404
    assert DependenciaNoDisponible().status == 503


def test_perfil_riesgo_se_construye_con_factores():
    perfil = PerfilRiesgo(
        cliente_id="cli-1",
        nivel_riesgo=NivelRiesgo.ALTO,
        factores=(FactorRiesgo("Endeudamiento alto", EfectoFactor.NEGATIVO, Decimal("0.4")),),
        factor_ajuste=Decimal("1.30"),
        fuentes_no_disponibles=(),
    )
    assert perfil.cliente_id == "cli-1"
    assert perfil.nivel_riesgo == NivelRiesgo.ALTO
    assert len(perfil.factores) == 1
    assert perfil.calculado_en is not None


def test_domain_event_genera_id_y_timestamp_por_defecto():
    evento = DomainEvent("PerfilCalculado", {"clienteId": "cli-1"})
    assert evento.tipo == "PerfilCalculado"
    assert evento.id
    assert evento.ocurrido_en is not None
