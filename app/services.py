"""Orquestación y cálculo de perfil de riesgo del servicio de Perfilamiento."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain import (
    ConsentimientoFuente,
    ConsultaGuardada,
    CreditosHipotecarios,
    DependenciaNoDisponible,
    DomainEvent,
    EfectoFactor,
    EstadoCreditos,
    FactorRiesgo,
    Fuente,
    NivelRiesgo,
    PerfilRiesgo,
    SenalOpenData,
    SenalOpenFinance,
    now_utc,
)
from app.logging_utils import sanear_para_log
from app.ports import (
    EventosPort,
    OpenDataPort,
    OpenFinancePort,
    PerfilRepositoryPort,
    SenalesRepositoryPort,
)

logger = logging.getLogger("perfilamiento.services")

_FACTOR_BASE = Decimal("1.00")
_FACTOR_MIN = Decimal("0.60")
_FACTOR_MAX = Decimal("1.80")


def calcular_perfil_riesgo(
    cliente_id: str,
    open_finance: SenalOpenFinance | None,
    open_data: SenalOpenData | None,
    fuentes_no_disponibles: tuple[str, ...],
) -> PerfilRiesgo:
    """Combina las señales disponibles en nivel de riesgo + factor de ajuste.

    Heurística de primer corte (no es un modelo actuarial real — caso
    académico): cada señal negativa suma al factor, cada señal positiva
    resta. Con degradación parcial (BITS-106 AC-2): si una fuente no
    respondió, simplemente no aporta factores — nunca bloquea el cálculo.
    """
    factor = _FACTOR_BASE
    factores: list[FactorRiesgo] = []

    if open_finance is not None:
        if open_finance.nivel_endeudamiento == "ALTO":
            factor += Decimal("0.30")
            factores.append(
                FactorRiesgo("Endeudamiento alto", EfectoFactor.NEGATIVO, Decimal("0.4"))
            )
        elif open_finance.nivel_endeudamiento == "BAJO":
            factor -= Decimal("0.10")
            factores.append(
                FactorRiesgo("Endeudamiento bajo", EfectoFactor.POSITIVO, Decimal("0.2"))
            )

        if open_finance.historial_pagos == "IRREGULAR":
            factor += Decimal("0.15")
            factores.append(
                FactorRiesgo("Historial de pagos irregular", EfectoFactor.NEGATIVO, Decimal("0.3"))
            )
        elif open_finance.historial_pagos == "EXCELENTE":
            factor -= Decimal("0.05")

    if open_data is not None:
        if open_data.categoria_salud_actuarial == "ELEVADA":
            factor += Decimal("0.20")
            factores.append(
                FactorRiesgo(
                    "Categoría de salud actuarial elevada", EfectoFactor.NEGATIVO, Decimal("0.3")
                )
            )
        elif open_data.categoria_salud_actuarial == "BAJA":
            factor -= Decimal("0.05")

    factor = min(max(factor, _FACTOR_MIN), _FACTOR_MAX)
    if factor < Decimal("0.95"):
        nivel = NivelRiesgo.BAJO
    elif factor <= Decimal("1.15"):
        nivel = NivelRiesgo.MEDIO
    else:
        nivel = NivelRiesgo.ALTO

    return PerfilRiesgo(
        cliente_id=cliente_id,
        nivel_riesgo=nivel,
        factores=tuple(factores),
        factor_ajuste=factor,
        fuentes_no_disponibles=fuentes_no_disponibles,
    )


class PerfilamientoService:
    """Orquesta las fuentes externas, lo que se guarda de cada una y el perfil.

    Cada fuente se consulta una vez al otorgarse su consentimiento y su señal se
    guarda con la fecha de consulta. La señal vale ``vigencia`` (24 h): pasado ese
    tiempo se reconsulta de forma perezosa, solo cuando alguien la necesita
    (``obtener_creditos_hipotecarios``). Revocar una fuente borra su consentimiento
    y su señal y recalcula el perfil con las demás, sin consultar a nadie.
    """

    def __init__(
        self,
        open_finance: OpenFinancePort,
        open_data: OpenDataPort,
        repositorio: PerfilRepositoryPort,
        eventos: EventosPort,
        senales: SenalesRepositoryPort,
        *,
        vigencia: timedelta = timedelta(hours=24),
        ahora: Callable[[], datetime] = now_utc,
    ) -> None:
        self._open_finance = open_finance
        self._open_data = open_data
        self._repositorio = repositorio
        self._eventos = eventos
        self._senales = senales
        self._vigencia = vigencia
        self._ahora = ahora
        # cliente -> [candado, esperando]: una sola reconsulta a la vez por cliente.
        self._refrescos: dict[str, list] = {}

    def _puerto(self, fuente: Fuente) -> OpenFinancePort | OpenDataPort:
        return self._open_finance if fuente is Fuente.OPEN_FINANCE else self._open_data

    # --- consumo de eventos ---

    async def procesar_registro(
        self,
        cliente_id: str,
        numero_documento: str,
        *,
        autoriza_open_finance: bool,
        autoriza_open_data: bool,
    ) -> PerfilRiesgo | None:
        """``ClienteRegistrado``: consulta solo las fuentes que el cliente autorizó."""
        fuentes = [
            fuente
            for fuente, autorizada in (
                (Fuente.OPEN_FINANCE, autoriza_open_finance),
                (Fuente.OPEN_DATA, autoriza_open_data),
            )
            if autorizada
        ]
        if not fuentes:
            logger.info(
                "procesar_registro: sin consentimientos cliente_id=%s", sanear_para_log(cliente_id)
            )
            return None
        return await self._otorgar(cliente_id, numero_documento, fuentes)

    async def otorgar_consentimiento(
        self, cliente_id: str, numero_documento: str, fuente: Fuente
    ) -> PerfilRiesgo | None:
        """``ConsentimientoOtorgado``: consulta solo la fuente consentida."""
        return await self._otorgar(cliente_id, numero_documento, [fuente])

    async def revocar_consentimiento(self, cliente_id: str, fuente: Fuente) -> None:
        """``ConsentimientoRevocado``: borra lo de esa fuente y recalcula con el resto."""
        logger.info(
            "revocar_consentimiento: cliente_id=%s fuente=%s", sanear_para_log(cliente_id), fuente
        )
        await self._senales.eliminar_consentimiento(cliente_id, fuente)
        await self._senales.eliminar_senal(cliente_id, fuente)
        await self._recalcular(cliente_id)

    async def calcular_perfil(self, cliente_id: str, numero_documento: str) -> PerfilRiesgo:
        """Consulta ambas fuentes y calcula el perfil (``POST /perfiles``)."""
        perfil = await self._otorgar(
            cliente_id, numero_documento, [Fuente.OPEN_FINANCE, Fuente.OPEN_DATA]
        )
        assert perfil is not None  # hay consentimiento sobre ambas fuentes
        return perfil

    async def obtener_perfil(self, cliente_id: str) -> PerfilRiesgo:
        logger.info("obtener_perfil: consultando cliente_id=%s", sanear_para_log(cliente_id))
        return await self._repositorio.obtener(cliente_id)

    async def invalidar_perfil(self, cliente_id: str) -> None:
        """Borra todo lo guardado del cliente (consentimientos, señales y perfil)."""
        logger.info("invalidar_perfil: cliente_id=%s", sanear_para_log(cliente_id))
        for fuente in Fuente:
            await self._senales.eliminar_consentimiento(cliente_id, fuente)
            await self._senales.eliminar_senal(cliente_id, fuente)
        await self._recalcular(cliente_id)

    # --- créditos hipotecarios (lectura para la pantalla previa a cotizar) ---

    async def obtener_creditos_hipotecarios(self, cliente_id: str) -> CreditosHipotecarios:
        """Hipotecas abiertas del cliente según lo guardado de Open Finance.

        No consulta a la fuente mientras la señal esté vigente. Si venció (o nunca
        llegó), reconsulta una vez; si eso falla, responde ``NO_DISPONIBLE`` y el
        cliente captura los datos a mano.
        """
        consentimiento = await self._senales.obtener_consentimiento(cliente_id, Fuente.OPEN_FINANCE)
        if consentimiento is None:
            return CreditosHipotecarios(EstadoCreditos.SIN_CONSENTIMIENTO)

        consulta = await self._senal_vigente(cliente_id, Fuente.OPEN_FINANCE)
        if consulta is None:
            async with self._un_solo_refresco(cliente_id):
                # Otra petición pudo refrescarla mientras esta esperaba el candado.
                consulta = await self._senal_vigente(cliente_id, Fuente.OPEN_FINANCE)
                if consulta is None:
                    consulta = await self._refrescar(cliente_id, consentimiento.numero_documento)
        if consulta is None:
            return CreditosHipotecarios(EstadoCreditos.NO_DISPONIBLE)

        senal = consulta.senal
        assert isinstance(senal, SenalOpenFinance)
        estado = EstadoCreditos.DISPONIBLE if senal.hipotecas else EstadoCreditos.SIN_HIPOTECAS
        return CreditosHipotecarios(estado, senal.hipotecas, consulta.consultado_en)

    # --- internos ---

    async def _otorgar(
        self, cliente_id: str, numero_documento: str, fuentes: list[Fuente]
    ) -> PerfilRiesgo | None:
        logger.info(
            "consultando fuentes en paralelo cliente_id=%s fuentes=%s",
            sanear_para_log(cliente_id),
            [str(f) for f in fuentes],
        )
        for fuente in fuentes:
            await self._senales.guardar_consentimiento(
                ConsentimientoFuente(cliente_id, fuente, numero_documento)
            )
        await self._consultar(cliente_id, numero_documento, fuentes)
        return await self._recalcular(cliente_id)

    async def _consultar(
        self, cliente_id: str, numero_documento: str, fuentes: list[Fuente]
    ) -> int:
        """Consulta las fuentes en paralelo y guarda lo que responda. Una fuente que no
        responde se omite (degradación parcial, BITS-106 AC-2); un error inesperado se
        propaga después de guardar lo que sí llegó. Devuelve cuántas guardó."""
        resultados = await asyncio.gather(
            *(self._puerto(f).consultar(numero_documento) for f in fuentes),
            return_exceptions=True,
        )
        consultado_en = self._ahora()
        guardadas = 0
        inesperado: BaseException | None = None
        for fuente, resultado in zip(fuentes, resultados, strict=True):
            if isinstance(resultado, DependenciaNoDisponible):
                logger.warning("%s no disponible, degradando parcialmente", fuente.nombre)
            elif isinstance(resultado, BaseException):
                inesperado = inesperado or resultado
            else:
                await self._senales.guardar_senal(
                    cliente_id, fuente, ConsultaGuardada(resultado, consultado_en)
                )
                guardadas += 1
        if inesperado is not None:
            raise inesperado
        return guardadas

    async def _recalcular(self, cliente_id: str) -> PerfilRiesgo | None:
        """Calcula el perfil con las señales guardadas de las fuentes con consentimiento.
        Sin ninguna fuente consentida no hay perfil: se borra y se avisa."""
        activas: list[Fuente] = []
        senales: dict[Fuente, SenalOpenFinance | SenalOpenData] = {}
        for fuente in Fuente:
            if await self._senales.obtener_consentimiento(cliente_id, fuente) is None:
                continue
            activas.append(fuente)
            consulta = await self._senales.obtener_senal(cliente_id, fuente)
            if consulta is not None:
                senales[fuente] = consulta.senal

        if not activas:
            await self._repositorio.eliminar(cliente_id)
            await self._eventos.publicar(DomainEvent("PerfilInvalidado", {"clienteId": cliente_id}))
            return None

        open_finance = senales.get(Fuente.OPEN_FINANCE)
        open_data = senales.get(Fuente.OPEN_DATA)
        assert open_finance is None or isinstance(open_finance, SenalOpenFinance)
        assert open_data is None or isinstance(open_data, SenalOpenData)
        perfil = calcular_perfil_riesgo(
            cliente_id,
            open_finance,
            open_data,
            tuple(f.nombre for f in activas if f not in senales),
        )
        await self._repositorio.guardar(perfil)
        await self._eventos.publicar(
            DomainEvent(
                "PerfilCalculado",
                {"clienteId": cliente_id, "nivelRiesgo": str(perfil.nivel_riesgo)},
            )
        )
        logger.info(
            "perfil calculado cliente_id=%s nivel_riesgo=%s fuentes_no_disponibles=%s",
            sanear_para_log(cliente_id),
            perfil.nivel_riesgo,
            perfil.fuentes_no_disponibles,
        )
        return perfil

    async def _senal_vigente(self, cliente_id: str, fuente: Fuente) -> ConsultaGuardada | None:
        consulta = await self._senales.obtener_senal(cliente_id, fuente)
        if consulta is None or self._ahora() - consulta.consultado_en >= self._vigencia:
            return None
        return consulta

    async def _refrescar(self, cliente_id: str, numero_documento: str) -> ConsultaGuardada | None:
        """Reconsulta Open Finance y, si también venció y tiene consentimiento, Open Data."""
        fuentes = [Fuente.OPEN_FINANCE]
        if (
            await self._senales.obtener_consentimiento(cliente_id, Fuente.OPEN_DATA) is not None
            and await self._senal_vigente(cliente_id, Fuente.OPEN_DATA) is None
        ):
            fuentes.append(Fuente.OPEN_DATA)
        if await self._consultar(cliente_id, numero_documento, fuentes):
            await self._recalcular(cliente_id)
        return await self._senal_vigente(cliente_id, Fuente.OPEN_FINANCE)

    @asynccontextmanager
    async def _un_solo_refresco(self, cliente_id: str):
        entrada = self._refrescos.setdefault(cliente_id, [asyncio.Lock(), 0])
        entrada[1] += 1
        try:
            async with entrada[0]:
                yield
        finally:
            entrada[1] -= 1
            if entrada[1] == 0:
                self._refrescos.pop(cliente_id, None)
