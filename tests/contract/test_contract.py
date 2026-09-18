"""Pruebas de contrato: el código cumple ``openapi/openapi.yaml``."""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator

_METODOS = {"get", "post", "put", "patch", "delete"}
_REF = re.compile(r"#/components/schemas/([A-Za-z0-9_]+)")
_PATH_PARAM = re.compile(r"\{[^}]+\}")

SOLICITUD_VALIDA = {"clienteId": "cli-1", "numeroDocumento": "1000000001"}


def _ops(paths: dict) -> set[tuple[str, str]]:
    return {
        (m.upper(), _PATH_PARAM.sub("{}", p))
        for p, item in paths.items()
        for m in item
        if m.lower() in _METODOS
    }


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


def test_spec_tiene_estructura_openapi(openapi_spec):
    assert openapi_spec["openapi"].startswith("3.")
    assert "post" in openapi_spec["paths"]["/perfiles"]


def test_referencias_de_schema_existen(openapi_spec):
    definidos = set(openapi_spec["components"]["schemas"])
    assert not set(_REF.findall(str(openapi_spec))) - definidos


def test_cada_schema_es_json_schema_valido(openapi_spec):
    for schema in openapi_spec["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)


def test_contrato_y_codigo_exponen_las_mismas_operaciones(app, openapi_spec):
    assert _ops(app.openapi()["paths"]) == _ops(openapi_spec["paths"])


async def test_respuesta_de_calcular_perfil_cumple_el_contrato(client, openapi_spec):
    resp = await client.post("/perfiles", json=SOLICITUD_VALIDA)
    assert resp.status_code == 201
    _validar(openapi_spec, "PerfilRiesgo", resp.json())


async def test_respuesta_de_obtener_perfil_cumple_el_contrato(client, openapi_spec):
    await client.post("/perfiles", json=SOLICITUD_VALIDA)
    resp = await client.get("/perfiles/cli-1")
    assert resp.status_code == 200
    _validar(openapi_spec, "PerfilRiesgo", resp.json())


async def test_error_cumple_problem_details(client, openapi_spec):
    resp = await client.get("/perfiles/no-existe")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    _validar(openapi_spec, "Problema", resp.json())


async def test_el_servicio_publica_el_contrato(client):
    resp = await client.get("/openapi.yaml")
    assert resp.status_code == 200
    assert "perfiles" in resp.text
