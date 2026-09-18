"""Modelos Pydantic de la API. Reflejan ``openapi/openapi.yaml``.

Los nombres de campo de salida (``nivelRiesgo``, ``factorAjuste``, ``factores``,
``fuentesNoDisponibles``) son el contrato ya consumido por ``svc-cotizacion``
(``app/adapters/perfilador_client.py::_a_perfil``) — no cambiar sin actualizar
ese parser también.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_DOCUMENTO = r"^[0-9A-Za-z-]+$"


class _Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


# --- entrada ---


class SolicitudPerfilIn(_Model):
    cliente_id: str = Field(min_length=1, max_length=64)
    numero_documento: str = Field(min_length=4, max_length=20, pattern=_DOCUMENTO)


# --- salida ---


class FactorRiesgoOut(_Model):
    descripcion: str
    efecto: Literal["POSITIVO", "NEGATIVO"]
    peso_relativo: float | None = None


class PerfilRiesgoOut(_Model):
    cliente_id: str
    nivel_riesgo: Literal["BAJO", "MEDIO", "ALTO"]
    factor_ajuste: float
    factores: list[FactorRiesgoOut]
    fuentes_no_disponibles: list[str] = Field(default_factory=list)
    calculado_en: datetime
