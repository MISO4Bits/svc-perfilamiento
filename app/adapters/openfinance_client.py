"""Adaptador HTTP hacia Open Finance (implementa ``OpenFinancePort``).

En dev/EXP-02 apunta al mock de WireMock (``deploy/apps/wiremock``), no a un
proveedor real — no existe integración real con ningún proveedor de Open
Finance para este caso académico.

  POST {base}/v1/perfil-crediticio
  body: {"numeroDocumento": "..."}
  200:  {"scoreCrediticio", "nivelEndeudamiento", "historialPagos", "productosActivos"}

La resiliencia (timeout 500 ms + reintentos + circuit breaker propio de esta
fuente) la aporta ``ResilientHttpClient``; ante timeout, 5xx o circuito
abierto lanza ``DependenciaNoDisponible``, que ``PerfilamientoService``
captura para degradar parcialmente (AC-2 de BITS-106).
"""

from __future__ import annotations

import logging

from app.domain import SenalOpenFinance
from app.resilience import ResilientHttpClient

logger = logging.getLogger("perfilamiento.adapters.openfinance")


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
        senal = SenalOpenFinance(
            score_crediticio=data["scoreCrediticio"],
            nivel_endeudamiento=data["nivelEndeudamiento"],
            historial_pagos=data["historialPagos"],
            productos_activos=data["productosActivos"],
        )
        logger.info(
            "Open Finance: señal recibida nivel_endeudamiento=%s historial_pagos=%s",
            senal.nivel_endeudamiento,
            senal.historial_pagos,
        )
        return senal
