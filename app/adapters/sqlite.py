"""Adaptador de persistencia sobre SQLite (aiosqlite).

Es el backend de desarrollo. Cuando se adopte Cloud Spanner (DI-009) se
implementan los mismos puertos con ese motor; el resto del servicio no cambia.

Tres tablas por cliente: el consentimiento vigente de cada fuente (con el
documento, para poder reconsultar), la última señal traída de cada fuente y el
perfil de riesgo calculado. SQLite no tiene TTL: la vigencia de las señales la
decide el servicio comparando ``consultado_en``.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

import aiosqlite

from app.domain import (
    ConsentimientoFuente,
    ConsultaGuardada,
    EfectoFactor,
    FactorRiesgo,
    Fuente,
    Hipoteca,
    NivelRiesgo,
    PerfilRiesgo,
    RecursoNoEncontrado,
    SenalOpenData,
    SenalOpenFinance,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consentimiento_fuente (
    cliente_id TEXT NOT NULL,
    fuente TEXT NOT NULL CHECK (fuente IN ('OPEN_FINANCE', 'OPEN_DATA')),
    numero_documento TEXT NOT NULL,
    PRIMARY KEY (cliente_id, fuente)
);
CREATE TABLE IF NOT EXISTS senal_fuente (
    cliente_id TEXT NOT NULL,
    fuente TEXT NOT NULL CHECK (fuente IN ('OPEN_FINANCE', 'OPEN_DATA')),
    contenido TEXT NOT NULL,
    consultado_en TEXT NOT NULL,
    PRIMARY KEY (cliente_id, fuente)
);
CREATE TABLE IF NOT EXISTS perfil_riesgo (
    cliente_id TEXT PRIMARY KEY,
    nivel_riesgo TEXT NOT NULL,
    factor_ajuste TEXT NOT NULL,
    factores TEXT NOT NULL,
    fuentes_no_disponibles TEXT NOT NULL,
    calculado_en TEXT NOT NULL
);
"""


class SqliteDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with self.connect() as conn:
            await conn.executescript(_SCHEMA)
            await conn.commit()

    @asynccontextmanager
    async def connect(self):
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()


# --- (de)serialización de señales ---


def _senal_a_json(senal: SenalOpenFinance | SenalOpenData) -> str:
    if isinstance(senal, SenalOpenFinance):
        return json.dumps(
            {
                "scoreCrediticio": senal.score_crediticio,
                "nivelEndeudamiento": senal.nivel_endeudamiento,
                "historialPagos": senal.historial_pagos,
                "productosActivos": senal.productos_activos,
                "hipotecas": [
                    {
                        "entidadAcreedora": h.entidad_acreedora,
                        "valorCredito": str(h.valor_credito),
                        "saldoInsoluto": str(h.saldo_insoluto),
                        "plazoRestanteMeses": h.plazo_restante_meses,
                        "cuotaMensual": str(h.cuota_mensual),
                    }
                    for h in senal.hipotecas
                ],
            }
        )
    return json.dumps(
        {
            "estrato": senal.estrato,
            "departamento": senal.departamento,
            "ciudad": senal.ciudad,
            "categoriaSaludActuarial": senal.categoria_salud_actuarial,
        }
    )


def _senal_de_json(fuente: Fuente, contenido: str) -> SenalOpenFinance | SenalOpenData:
    d = json.loads(contenido)
    if fuente is Fuente.OPEN_FINANCE:
        return SenalOpenFinance(
            score_crediticio=d["scoreCrediticio"],
            nivel_endeudamiento=d["nivelEndeudamiento"],
            historial_pagos=d["historialPagos"],
            productos_activos=d["productosActivos"],
            hipotecas=tuple(
                Hipoteca(
                    entidad_acreedora=h["entidadAcreedora"],
                    valor_credito=Decimal(h["valorCredito"]),
                    saldo_insoluto=Decimal(h["saldoInsoluto"]),
                    plazo_restante_meses=h["plazoRestanteMeses"],
                    cuota_mensual=Decimal(h["cuotaMensual"]),
                )
                for h in d["hipotecas"]
            ),
        )
    return SenalOpenData(
        estrato=d["estrato"],
        departamento=d["departamento"],
        ciudad=d["ciudad"],
        categoria_salud_actuarial=d["categoriaSaludActuarial"],
    )


class SqliteSenalesRepository:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    async def guardar_consentimiento(self, consentimiento: ConsentimientoFuente) -> None:
        async with self._db.connect() as conn:
            await conn.execute(
                "INSERT INTO consentimiento_fuente (cliente_id, fuente, numero_documento) "
                "VALUES (?, ?, ?) ON CONFLICT (cliente_id, fuente) "
                "DO UPDATE SET numero_documento = excluded.numero_documento",
                (
                    consentimiento.cliente_id,
                    str(consentimiento.fuente),
                    consentimiento.numero_documento,
                ),
            )
            await conn.commit()

    async def obtener_consentimiento(
        self, cliente_id: str, fuente: Fuente
    ) -> ConsentimientoFuente | None:
        async with self._db.connect() as conn:
            cursor = await conn.execute(
                "SELECT numero_documento FROM consentimiento_fuente "
                "WHERE cliente_id = ? AND fuente = ?",
                (cliente_id, str(fuente)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ConsentimientoFuente(cliente_id, fuente, row["numero_documento"])

    async def eliminar_consentimiento(self, cliente_id: str, fuente: Fuente) -> None:
        async with self._db.connect() as conn:
            await conn.execute(
                "DELETE FROM consentimiento_fuente WHERE cliente_id = ? AND fuente = ?",
                (cliente_id, str(fuente)),
            )
            await conn.commit()

    async def guardar_senal(
        self, cliente_id: str, fuente: Fuente, consulta: ConsultaGuardada
    ) -> None:
        async with self._db.connect() as conn:
            await conn.execute(
                "INSERT INTO senal_fuente (cliente_id, fuente, contenido, consultado_en) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (cliente_id, fuente) "
                "DO UPDATE SET contenido = excluded.contenido, "
                "consultado_en = excluded.consultado_en",
                (
                    cliente_id,
                    str(fuente),
                    _senal_a_json(consulta.senal),
                    consulta.consultado_en.isoformat(),
                ),
            )
            await conn.commit()

    async def obtener_senal(self, cliente_id: str, fuente: Fuente) -> ConsultaGuardada | None:
        async with self._db.connect() as conn:
            cursor = await conn.execute(
                "SELECT contenido, consultado_en FROM senal_fuente "
                "WHERE cliente_id = ? AND fuente = ?",
                (cliente_id, str(fuente)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ConsultaGuardada(
            senal=_senal_de_json(fuente, row["contenido"]),
            consultado_en=datetime.fromisoformat(row["consultado_en"]),
        )

    async def eliminar_senal(self, cliente_id: str, fuente: Fuente) -> None:
        async with self._db.connect() as conn:
            await conn.execute(
                "DELETE FROM senal_fuente WHERE cliente_id = ? AND fuente = ?",
                (cliente_id, str(fuente)),
            )
            await conn.commit()


class SqlitePerfilRepository:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    async def guardar(self, perfil: PerfilRiesgo) -> None:
        factores = [
            {
                "descripcion": f.descripcion,
                "efecto": str(f.efecto),
                "pesoRelativo": None if f.peso_relativo is None else str(f.peso_relativo),
            }
            for f in perfil.factores
        ]
        async with self._db.connect() as conn:
            await conn.execute(
                "INSERT INTO perfil_riesgo (cliente_id, nivel_riesgo, factor_ajuste, factores, "
                "fuentes_no_disponibles, calculado_en) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (cliente_id) DO UPDATE SET nivel_riesgo = excluded.nivel_riesgo, "
                "factor_ajuste = excluded.factor_ajuste, factores = excluded.factores, "
                "fuentes_no_disponibles = excluded.fuentes_no_disponibles, "
                "calculado_en = excluded.calculado_en",
                (
                    perfil.cliente_id,
                    str(perfil.nivel_riesgo),
                    str(perfil.factor_ajuste),
                    json.dumps(factores),
                    json.dumps(list(perfil.fuentes_no_disponibles)),
                    perfil.calculado_en.isoformat(),
                ),
            )
            await conn.commit()

    async def obtener(self, cliente_id: str) -> PerfilRiesgo:
        async with self._db.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM perfil_riesgo WHERE cliente_id = ?", (cliente_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise RecursoNoEncontrado(f"perfil de cliente_id={cliente_id} no encontrado")
        return PerfilRiesgo(
            cliente_id=row["cliente_id"],
            nivel_riesgo=NivelRiesgo(row["nivel_riesgo"]),
            factores=tuple(
                FactorRiesgo(
                    descripcion=f["descripcion"],
                    efecto=EfectoFactor(f["efecto"]),
                    peso_relativo=None if f["pesoRelativo"] is None else Decimal(f["pesoRelativo"]),
                )
                for f in json.loads(row["factores"])
            ),
            factor_ajuste=Decimal(row["factor_ajuste"]),
            fuentes_no_disponibles=tuple(json.loads(row["fuentes_no_disponibles"])),
            calculado_en=datetime.fromisoformat(row["calculado_en"]),
        )

    async def eliminar(self, cliente_id: str) -> None:
        async with self._db.connect() as conn:
            await conn.execute("DELETE FROM perfil_riesgo WHERE cliente_id = ?", (cliente_id,))
            await conn.commit()
