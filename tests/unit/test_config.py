from __future__ import annotations

from app.config import Settings, get_settings


def test_settings_por_defecto():
    s = Settings()
    assert s.service_name == "svc-perfilamiento"
    assert s.environment == "local"
    assert s.adapters == "fake"


def test_settings_lee_prefijo_perf(monkeypatch):
    monkeypatch.setenv("PERF_ADAPTERS", "http")
    assert Settings().adapters == "http"


def test_get_settings_cachea():
    assert get_settings() is get_settings()
