"""Adaptador HTTP hacia Open Finance (implementa ``OpenFinancePort``).

En dev/EXP-02 apunta al mock de WireMock (``deploy/apps/wiremock``), no a un
proveedor real — no existe integración real con ningún proveedor de Open
Finance para este caso académico.

  POST {base}/v1/perfil-crediticio
  body: {"numeroDocumento": "..."}
  200:  {"scoreCrediticio", "nivelEndeudamiento", "historialPagos", "productosActivos",
         "obligaciones": [{"tipoProducto", "estado", "entidadAcreedora", "valorCredito",
                           "saldoInsoluto", "plazoRestanteMeses", "cuotaMensual", ...}]}

Una sola consulta alimenta el perfil de riesgo y los créditos hipotecarios que se
le muestran al cliente al cotizar. Del reporte solo se conservan las hipotecas
abiertas y solo los campos que la pantalla necesita; el resto de las
obligaciones se descarta aquí.

La resiliencia (timeout 500 ms + reintentos + circuit breaker propio de esta
fuente) la aporta ``ResilientHttpClient``; ante timeout, 5xx o circuito
abierto lanza ``DependenciaNoDisponible``, que ``PerfilamientoService``
captura para degradar parcialmente (AC-2 de BITS-106).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.domain import Hipoteca, SenalOpenFinance
from app.resilience import ResilientHttpClient

logger = logging.getLogger("perfilamiento.adapters.openfinance")

_TIPO_HIPOTECARIO = "hipotecario"
_ESTADO_ABIERTA = "abierta"


def _a_hipoteca(obligacion: dict) -> Hipoteca:
    return Hipoteca(
        entidad_acreedora=obligacion["entidadAcreedora"],
        valor_credito=Decimal(str(obligacion["valorCredito"])),
        saldo_insoluto=Decimal(str(obligacion["saldoInsoluto"])),
        plazo_restante_meses=int(obligacion["plazoRestanteMeses"]),
        cuota_mensual=Decimal(str(obligacion["cuotaMensual"])),
    )


class OpenFinanceClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def consultar(self, numero_documento: str) -> SenalOpenFinance:
        resp = await self._http.request(
            "POST", "/v1/perfil-crediticio", json={"numeroDocumento": numero_documento}
        )
        data = resp.json()
        hipotecas = tuple(
            _a_hipoteca(o)
            for o in data.get("obligaciones", [])
            if o.get("tipoProducto") == _TIPO_HIPOTECARIO and o.get("estado") == _ESTADO_ABIERTA
        )
        senal = SenalOpenFinance(
            score_crediticio=data["scoreCrediticio"],
            nivel_endeudamiento=data["nivelEndeudamiento"],
            historial_pagos=data["historialPagos"],
            productos_activos=data["productosActivos"],
            hipotecas=hipotecas,
        )
        logger.info(
            "Open Finance: señal recibida nivel_endeudamiento=%s historial_pagos=%s hipotecas=%d",
            senal.nivel_endeudamiento,
            senal.historial_pagos,
            len(hipotecas),
        )
        return senal
