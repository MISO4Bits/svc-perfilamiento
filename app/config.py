from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración por variables de entorno (prefijo ``PERF_``)."""

    model_config = SettingsConfigDict(env_prefix="PERF_", env_file=".env", extra="ignore")

    service_name: str = "svc-perfilamiento"
    environment: str = "local"

    # Adaptadores de salida: "fake" (todo en memoria) | "http" (Open Finance/
    # Open Data reales — en dev, el mock de WireMock, ver deploy/apps/wiremock).
    adapters: str = "fake"

    # Modo "http": cada fuente es un host/prefijo distinto aunque ambas
    # apunten hoy al mismo pod de WireMock (paths /open-finance, /open-data).
    open_finance_base_url: str = "http://localhost:8090/open-finance"
    open_data_base_url: str = "http://localhost:8090/open-data"
    fuente_timeout_seconds: float = 0.5  # timeout duro por fuente (BITS-106)
    fuente_retries: int = 1
    circuit_fail_max: int = 5
    circuit_reset_timeout_seconds: int = 30

    # Publicación de eventos de dominio (``PerfilCalculado``) y consumo de
    # ConsentimientoOtorgado/ConsentimientoRevocado: "logging" (default,
    # local/tests, sin consumidor) | "pubsub" (real — también arranca el
    # consumidor, ver adapters/pubsub_consumer.py). Un único tópico
    # compartido con CoreTransaccional (ver iac-gcp-dev/modules/pubsub).
    event_backend: str = "logging"
    pubsub_project_id: str | None = None
    pubsub_topic: str = "solventa-dominio"
    pubsub_subscription: str = "perfilamiento-consentimiento"

    # Observabilidad (DI-008): OTLP/gRPC hacia Grafana Alloy dentro del
    # cluster. Deshabilitado por defecto — en local/tests no hay receptor
    # escuchando; se habilita vía PERF_OTEL_ENABLED=true en el manifiesto de
    # despliegue.
    otel_enabled: bool = False
    otel_exporter_endpoint: str = (
        "k8s-monitoring-alloy-receiver.observability.svc.cluster.local:4317"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
