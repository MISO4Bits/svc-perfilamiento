"""Fábrica de adaptadores del servicio de Perfilamiento (sin framework de DI)."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.fakes import (
    FakeOpenData,
    FakeOpenFinance,
    FakePerfilRepository,
    FakeSenalesRepository,
    LoggingEventos,
)
from app.adapters.opendata_client import OpenDataClientAdapter
from app.adapters.openfinance_client import OpenFinanceClientAdapter
from app.adapters.sqlite import SqliteDatabase, SqlitePerfilRepository, SqliteSenalesRepository
from app.config import Settings
from app.ports import (
    EventosPort,
    OpenDataPort,
    OpenFinancePort,
    PerfilRepositoryPort,
    SenalesRepositoryPort,
)
from app.resilience import ResilientHttpClient, build_breaker


@dataclass
class Dependencias:
    open_finance: OpenFinancePort
    open_data: OpenDataPort
    repositorio: PerfilRepositoryPort
    senales: SenalesRepositoryPort
    eventos: EventosPort
    database: SqliteDatabase | None = None

    async def init(self) -> None:
        """Crea el esquema si la persistencia está respaldada por SQLite."""
        if self.database is not None:
            await self.database.init()

    async def aclose(self) -> None:
        for adaptador in (self.open_finance, self.open_data):
            cerrar = getattr(adaptador, "aclose", None)
            if cerrar is not None:
                await cerrar()


def build_eventos(settings: Settings) -> EventosPort:
    if settings.event_backend == "pubsub":  # pragma: no cover
        from app.adapters.pubsub import PubSubEventPublisher

        return PubSubEventPublisher(settings.pubsub_project_id or "", settings.pubsub_topic)
    return LoggingEventos()


def build_persistencia(
    settings: Settings,
) -> tuple[PerfilRepositoryPort, SenalesRepositoryPort, SqliteDatabase | None]:
    if settings.repository_backend == "memory":
        return FakePerfilRepository(), FakeSenalesRepository(), None
    if settings.repository_backend == "sqlite":
        db = SqliteDatabase(settings.database_path)
        return SqlitePerfilRepository(db), SqliteSenalesRepository(db), db
    raise ValueError(f"repository_backend no soportado: {settings.repository_backend}")


def build_dependencias(settings: Settings) -> Dependencias:
    repositorio, senales, database = build_persistencia(settings)
    eventos = build_eventos(settings)

    if settings.adapters == "fake":
        return Dependencias(
            open_finance=FakeOpenFinance(),
            open_data=FakeOpenData(),
            repositorio=repositorio,
            senales=senales,
            eventos=eventos,
            database=database,
        )

    if settings.adapters == "http":
        http_of = ResilientHttpClient(
            settings.open_finance_base_url,
            breaker=build_breaker(
                "open-finance",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.fuente_timeout_seconds,
            retries=settings.fuente_retries,
        )
        http_od = ResilientHttpClient(
            settings.open_data_base_url,
            breaker=build_breaker(
                "open-data",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.fuente_timeout_seconds,
            retries=settings.fuente_retries,
        )
        return Dependencias(
            open_finance=OpenFinanceClientAdapter(http_of),
            open_data=OpenDataClientAdapter(http_od),
            repositorio=repositorio,
            senales=senales,
            eventos=eventos,
            database=database,
        )

    raise ValueError(f"adapters no soportado: {settings.adapters}")
