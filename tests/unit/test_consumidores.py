"""Pruebas del despacho de eventos de consentimiento — sin GCP (la mecánica
de transporte real vive en adapters/pubsub_consumer.py, excluida de
cobertura)."""

from __future__ import annotations

from app.consumidores import despachar_evento
from app.domain import DomainEvent, Fuente


class _ServicioEspia:
    def __init__(self) -> None:
        self.registros: list[tuple] = []
        self.otorgados: list[tuple[str, str, Fuente]] = []
        self.revocados: list[tuple[str, Fuente]] = []

    async def procesar_registro(
        self,
        cliente_id: str,
        numero_documento: str,
        *,
        autoriza_open_finance: bool,
        autoriza_open_data: bool,
    ) -> None:
        self.registros.append(
            (cliente_id, numero_documento, autoriza_open_finance, autoriza_open_data)
        )

    async def otorgar_consentimiento(
        self, cliente_id: str, numero_documento: str, fuente: Fuente
    ) -> None:
        self.otorgados.append((cliente_id, numero_documento, fuente))

    async def revocar_consentimiento(self, cliente_id: str, fuente: Fuente) -> None:
        self.revocados.append((cliente_id, fuente))


def _sin_efectos(servicio: _ServicioEspia) -> bool:
    return not (servicio.registros or servicio.otorgados or servicio.revocados)


async def test_cliente_registrado_pasa_el_consentimiento_de_cada_fuente():
    servicio = _ServicioEspia()
    evento = DomainEvent(
        "ClienteRegistrado",
        {
            "clienteId": "cli-1",
            "tipoDocumento": "CC",
            "numeroDocumento": "1000000001",
            "autorizaTratamientoDatos": False,
            "autorizaDatosFinancieros": True,
        },
    )

    await despachar_evento(servicio, evento)

    # Datos financieros -> Open Finance; tratamiento de datos -> Open Data.
    assert servicio.registros == [("cli-1", "1000000001", True, False)]
    assert servicio.otorgados == []


async def test_consentimiento_otorgado_consulta_solo_la_fuente_del_scope():
    servicio = _ServicioEspia()
    evento = DomainEvent(
        "ConsentimientoOtorgado",
        {
            "clienteId": "cli-1",
            "scope": "OPEN_DATA",
            "version": 1,
            "tipoDocumento": "CC",
            "numeroDocumento": "1000000001",
        },
    )

    await despachar_evento(servicio, evento)

    assert servicio.otorgados == [("cli-1", "1000000001", Fuente.OPEN_DATA)]
    assert servicio.registros == []


async def test_consentimiento_revocado_borra_solo_la_fuente_del_scope():
    servicio = _ServicioEspia()
    evento = DomainEvent(
        "ConsentimientoRevocado", {"clienteId": "cli-1", "scope": "OPEN_FINANCE", "version": 2}
    )

    await despachar_evento(servicio, evento)

    assert servicio.revocados == [("cli-1", Fuente.OPEN_FINANCE)]


async def test_scope_desconocido_no_hace_nada():
    servicio = _ServicioEspia()
    for tipo in ("ConsentimientoOtorgado", "ConsentimientoRevocado"):
        await despachar_evento(
            servicio,
            DomainEvent(tipo, {"clienteId": "cli-1", "scope": "OTRO", "numeroDocumento": "1234"}),
        )

    assert _sin_efectos(servicio)


async def test_tipo_de_evento_no_reconocido_no_hace_nada():
    servicio = _ServicioEspia()
    evento = DomainEvent("OtroEvento", {})

    await despachar_evento(servicio, evento)

    assert _sin_efectos(servicio)
