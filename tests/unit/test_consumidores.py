"""Pruebas del despacho de eventos de consentimiento — sin GCP (la mecánica
de transporte real vive en adapters/pubsub_consumer.py, excluida de
cobertura)."""

from __future__ import annotations

from app.consumidores import despachar_evento
from app.domain import DomainEvent


class _ServicioEspia:
    def __init__(self) -> None:
        self.calculados: list[tuple[str, str]] = []
        self.invalidados: list[str] = []

    async def calcular_perfil(self, cliente_id: str, numero_documento: str) -> None:
        self.calculados.append((cliente_id, numero_documento))

    async def invalidar_perfil(self, cliente_id: str) -> None:
        self.invalidados.append(cliente_id)


async def test_consentimiento_otorgado_calcula_el_perfil():
    servicio = _ServicioEspia()
    evento = DomainEvent(
        "ConsentimientoOtorgado",
        {
            "clienteId": "cli-1",
            "scope": "OPEN_FINANCE",
            "version": 1,
            "tipoDocumento": "CC",
            "numeroDocumento": "1000000001",
        },
    )

    await despachar_evento(servicio, evento)

    assert servicio.calculados == [("cli-1", "1000000001")]
    assert servicio.invalidados == []


async def test_consentimiento_revocado_invalida_el_perfil():
    servicio = _ServicioEspia()
    evento = DomainEvent(
        "ConsentimientoRevocado", {"clienteId": "cli-1", "scope": "OPEN_FINANCE", "version": 2}
    )

    await despachar_evento(servicio, evento)

    assert servicio.invalidados == ["cli-1"]
    assert servicio.calculados == []


async def test_tipo_de_evento_no_reconocido_no_hace_nada():
    servicio = _ServicioEspia()
    evento = DomainEvent("OtroEvento", {})

    await despachar_evento(servicio, evento)

    assert servicio.calculados == []
    assert servicio.invalidados == []
