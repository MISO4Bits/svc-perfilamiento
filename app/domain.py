"""Modelo de dominio del servicio de Perfilamiento.

DTO de dominio (dataclasses congeladas, sin framework) + errores de aplicación.
La validación de JSON de entrada vive en ``api/schemas.py`` (Pydantic); acá solo
van los tipos del dominio y sus invariantes de consistencia.

Diseño de referencia: página "Perfilamiento" de David (Confluence, 4BITS).
El perfil se calcula de forma asíncrona (disparado, en el diseño objetivo, por
el evento ``ConsentimientoOtorgado`` de CoreTransaccional — hoy expuesto vía
``POST /perfiles`` hasta que exista el consumidor real de Pub/Sub) y luego se
consulta de forma síncrona y barata (``GET /perfiles/{cliente_id}``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


# --- errores de aplicación (se traducen a RFC 9457 en la capa API) ---


class PerfilError(Exception):
    status = 500
    title = "Error interno"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail


class SolicitudInvalida(PerfilError):
    status = 400
    title = "Solicitud inválida"


class RecursoNoEncontrado(PerfilError):
    status = 404
    title = "Recurso no encontrado"


class DependenciaNoDisponible(PerfilError):
    status = 503
    title = "Dependencia no disponible"


# --- enumeraciones del dominio ---


class NivelRiesgo(StrEnum):
    BAJO = "BAJO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"


class EfectoFactor(StrEnum):
    POSITIVO = "POSITIVO"
    NEGATIVO = "NEGATIVO"


# --- señales de las fuentes externas (Open Finance / Open Data) ---


@dataclass(frozen=True)
class SenalOpenFinance:
    score_crediticio: int
    nivel_endeudamiento: str
    historial_pagos: str
    productos_activos: int


@dataclass(frozen=True)
class SenalOpenData:
    estrato: int
    departamento: str
    ciudad: str
    categoria_salud_actuarial: str


# --- perfil de riesgo (lo que persiste y lo que consulta Cotización) ---


@dataclass(frozen=True)
class FactorRiesgo:
    descripcion: str
    efecto: EfectoFactor
    peso_relativo: Decimal | None = None


@dataclass(frozen=True)
class PerfilRiesgo:
    cliente_id: str
    nivel_riesgo: NivelRiesgo
    factores: tuple[FactorRiesgo, ...]
    factor_ajuste: Decimal  # multiplicador que Cotización aplica a la prima base
    fuentes_no_disponibles: tuple[str, ...] = ()
    calculado_en: datetime = field(default_factory=now_utc)


# --- eventos de dominio ---


@dataclass
class DomainEvent:
    tipo: str
    datos: dict
    id: str = field(default_factory=new_id)
    ocurrido_en: datetime = field(default_factory=now_utc)
