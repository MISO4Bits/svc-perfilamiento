"""Adaptador HTTP hacia Open Data (implementa ``OpenDataPort``).

En dev/EXP-02 apunta al mock de WireMock (``deploy/apps/wiremock``), no a un
proveedor real. Misma política de resiliencia que ``OpenFinanceClientAdapter``.

  POST {base}/v1/perfil-sociodemografico
  body: {"numeroDocumento": "..."}
  200:  {"estrato", "ubicacion": {"departamento", "ciudad"}, "categoriaSaludActuarial"}
"""

from __future__ import annotations

import logging

from app.domain import SenalOpenData
from app.resilience import ResilientHttpClient

logger = logging.getLogger("perfilamiento.adapters.opendata")


class OpenDataClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def consultar(self, numero_documento: str) -> SenalOpenData:
        resp = await self._http.request(
            "POST", "/v1/perfil-sociodemografico", json={"numeroDocumento": numero_documento}
        )
        data = resp.json()
        ubicacion = data["ubicacion"]
        senal = SenalOpenData(
            estrato=data["estrato"],
            departamento=ubicacion["departamento"],
            ciudad=ubicacion["ciudad"],
            categoria_salud_actuarial=data["categoriaSaludActuarial"],
        )
        logger.info(
            "Open Data: señal recibida estrato=%s categoria_salud_actuarial=%s",
            senal.estrato,
            senal.categoria_salud_actuarial,
        )
        return senal
