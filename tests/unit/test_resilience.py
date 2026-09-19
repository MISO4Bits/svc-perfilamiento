from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.domain import DependenciaNoDisponible
from app.resilience import CircuitBreakerError, ResilientHttpClient, build_breaker

BASE = "http://dep.local"


def _cliente(retries: int = 2, fail_max: int = 5) -> ResilientHttpClient:
    return ResilientHttpClient(
        BASE,
        breaker=build_breaker("dep", fail_max=fail_max, reset_timeout=30),
        timeout=0.2,
        retries=retries,
    )


@respx.mock
async def test_devuelve_respuesta_en_camino_feliz():
    ruta = respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200, json={"ok": True}))
    http = _cliente()
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 1


@respx.mock
async def test_reintenta_ante_503_y_luego_tiene_exito():
    ruta = respx.get(f"{BASE}/ping").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200)]
    )
    http = _cliente(retries=2)
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 3


@respx.mock
async def test_agota_reintentos_y_lanza_dependencia_no_disponible():
    respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(503))
    http = _cliente(retries=1, fail_max=99)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")
    finally:
        await http.aclose()


@respx.mock
async def test_reintenta_ante_error_de_conexion():
    ruta = respx.get(f"{BASE}/ping").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200)]
    )
    http = _cliente(retries=2, fail_max=99)
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 2


@respx.mock
async def test_circuito_se_abre_y_falla_rapido():
    ruta = respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(503))
    http = _cliente(retries=2, fail_max=2)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")  # abre el circuito tras 2 fallos
        llamadas_tras_apertura = ruta.call_count

        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")  # circuito abierto: no toca la red
    finally:
        await http.aclose()

    assert llamadas_tras_apertura == 2
    assert ruta.call_count == 2  # la segunda petición no llamó al backend


@respx.mock
async def test_timeout_agotado_no_se_reintenta():
    """Un timeout ya gastó el presupuesto de latencia: reintentar lo duplicaría
    (EXP-02: 500 ms + 1 reintento = p99 de 1.04 s)."""
    ruta = respx.get(f"{BASE}/lento").mock(side_effect=httpx.ReadTimeout("timeout"))
    http = _cliente(retries=2, fail_max=99)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/lento")
    finally:
        await http.aclose()
    assert ruta.call_count == 1


@respx.mock
async def test_timeout_de_conexion_tampoco_se_reintenta():
    ruta = respx.get(f"{BASE}/lento").mock(side_effect=httpx.ConnectTimeout("timeout"))
    http = _cliente(retries=2, fail_max=99)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/lento")
    finally:
        await http.aclose()
    assert ruta.call_count == 1


def test_circuito_pasa_a_half_open_cuando_expira_el_reset():
    breaker = build_breaker("dep", fail_max=1, reset_timeout=0.0)
    breaker._registrar_fallo()  # abre el circuito
    assert breaker.state == "half_open"


@respx.mock
async def test_pool_agotado_falla_rapido_sin_reintentar():
    """Reintentar ante PoolTimeout solo suma más espera al mismo cuello de
    botella (EXP-01: p99 de 4.67 s por la cascada espera+reintento)."""
    ruta = respx.get(f"{BASE}/ping").mock(side_effect=httpx.PoolTimeout("sin conexiones libres"))
    http = _cliente(retries=2)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert ruta.call_count == 1


async def test_pool_y_timeouts_configurados_explicitamente():
    http = ResilientHttpClient(
        BASE,
        breaker=build_breaker("dep", fail_max=5, reset_timeout=30),
        timeout=0.5,
        pool_timeout=0.05,
        max_connections=150,
        max_keepalive_connections=75,
    )
    try:
        assert http._client.timeout.read == 0.5
        assert http._client.timeout.pool == 0.05
    finally:
        await http.aclose()


async def test_half_open_deja_pasar_una_sola_sonda():
    """En semiabierto solo una llamada prueba la dependencia; el resto falla
    rápido (EXP-02: antes todas pasaban y causaban una ráfaga lenta)."""
    breaker = build_breaker("dep", fail_max=1, reset_timeout=0.0)
    breaker._registrar_fallo()
    assert breaker.state == "half_open"
    liberar = asyncio.Event()
    llamadas = 0

    async def lenta() -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        await liberar.wait()
        return httpx.Response(200)

    sonda = asyncio.create_task(breaker.call(lenta))
    await asyncio.sleep(0)  # la sonda arranca y queda en curso
    with pytest.raises(CircuitBreakerError):
        await breaker.call(lenta)
    liberar.set()
    await sonda
    assert llamadas == 1
    assert breaker.state == "closed"


async def test_sonda_fallida_reabre_el_circuito():
    breaker = build_breaker("dep", fail_max=1, reset_timeout=30)
    breaker._registrar_fallo()
    breaker._opened_at -= 31  # venció el reset: pasa a semiabierto
    assert breaker.state == "half_open"

    async def falla() -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(httpx.ReadTimeout):
        await breaker.call(falla)
    assert breaker.state == "open"
    with pytest.raises(CircuitBreakerError):
        await breaker.call(falla)  # ya no se intenta hasta el próximo reset
