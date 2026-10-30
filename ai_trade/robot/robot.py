"""Robô: mesma regra do backtest, operando ao vivo.

A cada candle fechado: zera a posição do candle anterior, pergunta à IA e abre
a nova posição, que fica aberta por um candle.

Estados:
  STOPPED  desligado, sem posição do robô
  RUNNING  operando
  PAUSED   não abre posições; a que estiver aberta segue até o fim do candle
"""
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from enum import Enum
from typing import Callable

from ..domain import TIMEFRAMES, Direction, Position, Prediction
from ..interfaces import AIPredictor, MarketDataProvider, OrderExecutor, PromptBuilder, QuoteProvider

log = logging.getLogger(__name__)

MODES = ("paper", "mt5")


class RobotState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass(frozen=True)
class RobotParams:
    symbol: str
    timeframe: str
    bars: int = 50
    volume: float = 1.0
    stop_points: float = 300.0            # stop enviado junto com a ordem (protege se o robô cair)
    min_confidence: float = 0.6           # abaixo disso não opera
    max_trades_per_day: int = 20
    max_daily_loss: float = 500.0         # em dinheiro; ao atingir, o robô pausa
    max_signal_delay: float = 60.0        # segundos após a abertura do candle; depois disso o sinal é descartado
    mode: str = "paper"                   # paper = simulado, mt5 = ordens no terminal

    def __post_init__(self):
        if not self.symbol.strip():
            raise ValueError("Informe o ativo.")
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"Tempo gráfico inválido. Use um de: {', '.join(TIMEFRAMES)}")
        if self.mode not in MODES:
            raise ValueError("Modo inválido.")
        if self.bars < 1 or self.volume <= 0 or self.max_trades_per_day < 1:
            raise ValueError("Candles, volume e máximo de trades devem ser maiores que zero.")
        if self.stop_points <= 0:
            raise ValueError("O stop é obrigatório: ele protege a posição se o robô ou o PC cair.")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("A confiança mínima deve estar entre 0 e 1.")
        if self.max_daily_loss <= 0 or self.max_signal_delay <= 0:
            raise ValueError("Perda máxima e atraso máximo devem ser maiores que zero.")


LOCKED_WHILE_ACTIVE = {"symbol", "timeframe", "mode"}


@dataclass
class RobotSession:
    """Dependências de uma execução, montadas no composition root."""
    data: MarketDataProvider
    quotes: QuoteProvider
    executor: OrderExecutor
    predictor: AIPredictor
    prompt_builder: PromptBuilder
    close: Callable[[], None] = lambda: None


class TradingRobot:
    def __init__(self, defaults: RobotParams, open_session: Callable[[str], RobotSession],
                 allow_real_account: bool = False, poll_seconds: float = 1.0):
        self._params = defaults
        self._open_session = open_session
        self._allow_real = allow_real_account
        self._poll = poll_seconds
        self._state = RobotState.STOPPED
        self._reason = ""
        self._session: RobotSession | None = None
        self._stop_event = threading.Event()
        self._orders = threading.RLock()           # entrada, zeragem e troca de estado não podem se cruzar
        self._last_closed = None
        self._open_position: Position | None = None
        self._last_signal: dict | None = None
        self._ai_busy = False
        self._trades_today = 0
        self._day = datetime.now().date()
        self._events = deque(maxlen=200)
        self._trades = deque(maxlen=200)

    # --- comandos -----------------------------------------------------------------------------

    def command(self, msg: dict) -> None:
        actions = {
            "start": lambda: self.start(msg.get("params")),
            "stop": self.stop, "pause": self.pause, "resume": self.resume, "flatten": self.flatten,
            "update": lambda: self.update_params(msg.get("params") or {}),
        }
        action = msg.get("action")
        if action not in actions:
            raise ValueError(f"Comando desconhecido: {action}")
        actions[action]()

    def start(self, params: dict | None = None) -> None:
        with self._orders:
            if self._state is not RobotState.STOPPED:
                raise ValueError("O robô já está ligado.")
            target = self._merged(params or {})
            session = self._open_session(target.mode)
            try:
                self._check_can_trade(session, target)
                existing = session.executor.position(target.symbol)
            except Exception:
                session.close()
                raise
            self._session, self._params = session, target
            self._open_position, self._last_closed = existing, None
            self._state, self._reason = RobotState.RUNNING, ""
            self._stop_event = threading.Event()
            self._log(f"Robô ligado ({target.mode}) em {target.symbol} {target.timeframe}. "
                      "A primeira análise será no próximo candle fechado.")
            if existing:
                self._log("Posição do robô já aberta foi assumida; será zerada no fim do candle.")
            threading.Thread(target=self._loop, args=(self._stop_event,), name="robot", daemon=True).start()

    def stop(self) -> None:
        """Desliga e zera a posição do robô, para não deixar posição sem gestão."""
        with self._orders:
            if self._state is RobotState.STOPPED:
                return
            self._stop_event.set()
            self._close_position("desligamento")
            self._state, self._reason = RobotState.STOPPED, ""
            session, self._session = self._session, None
            self._log("Robô desligado.")
        session.close()

    def pause(self, reason: str = "") -> None:
        with self._orders:
            if self._state is RobotState.RUNNING:
                self._state, self._reason = RobotState.PAUSED, reason
                self._log(f"Robô pausado{': ' + reason if reason else ''}.")

    def resume(self) -> None:
        with self._orders:
            if self._state is RobotState.PAUSED:
                self._state, self._reason = RobotState.RUNNING, ""
                self._log("Robô retomado.")

    def flatten(self) -> None:
        """Zera agora e pausa; sem pausar, o robô entraria de novo no candle seguinte."""
        with self._orders:
            if self._state is RobotState.STOPPED:
                raise ValueError("O robô está desligado.")
            self._close_position("zeragem manual")
            self.pause("zeragem manual")

    def update_params(self, changes: dict) -> None:
        with self._orders:
            if self._state is not RobotState.STOPPED:
                locked = {k for k in changes if k in LOCKED_WHILE_ACTIVE and changes[k] != getattr(self._params, k)}
                if locked:
                    raise ValueError("Ativo, tempo gráfico e modo só mudam com o robô desligado.")
            self._params = self._merged(changes)
            self._log("Parâmetros atualizados.")

    # --- leitura ------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        session, params = self._session, self._params
        account = position = None
        pnl_today = 0.0
        error = None
        if session:
            try:
                account = asdict(session.executor.account())
                current = session.executor.position(params.symbol)
                position = self._position_view(current)
                pnl_today = session.executor.realized_today(params.symbol)
            except Exception as exc:
                error = str(exc)
        return {
            "state": self._state.value, "reason": self._reason, "params": asdict(params),
            "account": account, "position": position, "pnl_today": round(pnl_today, 2),
            "trades_today": self._trades_today, "ai_busy": self._ai_busy, "last_signal": self._last_signal,
            "events": list(self._events)[-50:], "trades": list(self._trades)[-50:], "error": error,
        }

    # --- ciclo --------------------------------------------------------------------------------

    def _loop(self, stop: threading.Event) -> None:
        while not stop.wait(self._poll):
            try:
                self._tick(stop)
            except Exception as exc:
                log.exception("Erro no ciclo do robô")
                self._log(f"Erro: {exc}", "error")

    def _tick(self, stop: threading.Event) -> None:
        session, p = self._session, self._params
        if session is None:
            return
        self._detect_external_close(session, p)

        last = session.data.get_last_bars(p.symbol, p.timeframe, 1)["time"].iloc[-1]
        if self._last_closed is None:
            self._last_closed = last      # entrou no meio do candle: espera o próximo fechar
            return
        if last == self._last_closed:
            return
        self._last_closed = last

        with self._orders:
            self._close_position("fim do candle")
        if self._state is RobotState.RUNNING:
            self._trade_candle(session, stop)

    def _trade_candle(self, session: RobotSession, stop: threading.Event) -> None:
        self._reset_day()
        p = self._params
        blocked = self._blocked(session, p)
        if blocked:
            self._log(blocked, "warn")
            return

        builder = session.prompt_builder
        candles = session.data.get_last_bars(p.symbol, p.timeframe, p.bars + builder.warmup_bars)
        started = datetime.now()
        self._ai_busy = True
        try:
            prediction = session.predictor.predict(builder.build(p.symbol, p.timeframe, candles, p.bars))
        finally:
            self._ai_busy = False
        delay = (datetime.now() - started).total_seconds()
        self._record_signal(prediction, candles["time"].iloc[-1], delay)

        if prediction.direction is Direction.NEUTRAL or prediction.confidence < p.min_confidence:
            self._log(f"Sem entrada: {prediction.direction.value} com confiança {prediction.confidence:.0%}.")
            return

        with self._orders:     # a resposta pode levar minutos: confere de novo depois dela
            p = self._params
            if stop.is_set() or self._state is not RobotState.RUNNING:
                self._log("Sinal descartado: o robô foi pausado ou desligado enquanto a IA respondia.", "warn")
            elif delay > p.max_signal_delay:
                self._log(f"Sinal descartado: a IA levou {delay:.0f}s (máximo {p.max_signal_delay:.0f}s).", "warn")
            else:
                self._open_position = session.executor.open(p.symbol, prediction.direction, p.volume, p.stop_points)
                self._trades_today += 1
                self._log(f"{self._side(prediction.direction)} {p.volume:g} {p.symbol} "
                          f"a {self._open_position.entry_price:g}.")

    def _blocked(self, session: RobotSession, p: RobotParams) -> str | None:
        if self._trades_today >= p.max_trades_per_day:
            self.pause(f"limite de {p.max_trades_per_day} trades no dia")
            return "Limite de trades do dia atingido."
        if session.executor.realized_today(p.symbol) <= -p.max_daily_loss:
            self.pause(f"perda máxima do dia ({p.max_daily_loss:g}) atingida")
            return "Perda máxima do dia atingida."
        if session.executor.has_foreign_position(p.symbol):
            return f"Sem entrada: há posição de outra origem em {p.symbol}."
        # candle novo já indica negociação; aqui basta garantir que há oferta dos dois lados
        quote = session.quotes.get_quote(p.symbol)
        if quote.bid <= 0 or quote.ask <= 0:
            return f"Sem entrada: {p.symbol} sem oferta de compra e venda agora."
        return None

    def _close_position(self, reason: str) -> None:
        session, opened = self._session, self._open_position
        if session is None:
            return
        closed = session.executor.close(self._params.symbol)
        if closed is None:
            return
        self._open_position = None
        side = self._side(opened.side) if opened else "?"
        entry = opened.entry_price if opened else None
        self._trades.append({"time": f"{datetime.now():%H:%M:%S}", "side": side, "entry": entry,
                             "exit": closed.exit_price, "profit": round(closed.profit, 2), "reason": reason})
        self._log(f"Posição zerada a {closed.exit_price:g} ({reason}): {closed.profit:+.2f}.")

    def _detect_external_close(self, session: RobotSession, p: RobotParams) -> None:
        """O stop é executado pela corretora (ou a posição foi zerada à mão no terminal)."""
        with self._orders:
            if self._open_position and session.executor.position(p.symbol) is None:
                self._log("Posição encerrada fora do robô (stop ou zeragem no terminal).", "warn")
                self._open_position = None

    def _check_can_trade(self, session: RobotSession, p: RobotParams) -> None:
        account = session.executor.account()
        if account.mode == "REAL" and not self._allow_real:
            raise ValueError("Conta REAL bloqueada. Para liberar, defina robot.allow_real_account: true no config.yaml.")
        if not account.trading_enabled:
            raise ValueError(account.detail or "A conta não permite operar.")
        session.executor.ensure_tradable(p.symbol)

    def _merged(self, changes: dict) -> RobotParams:
        unknown = set(changes) - {f.name for f in fields(RobotParams)}
        if unknown:
            raise ValueError(f"Parâmetros desconhecidos: {', '.join(sorted(unknown))}")
        return replace(self._params, **changes)

    def _record_signal(self, prediction: Prediction, candle, delay: float) -> None:
        self._last_signal = {"time": f"{datetime.now():%H:%M:%S}", "candle": f"{candle:%Y-%m-%d %H:%M}",
                             "direction": prediction.direction.value, "confidence": prediction.confidence,
                             "reasoning": prediction.reasoning, "delay": round(delay, 1)}

    def _reset_day(self) -> None:
        today = datetime.now().date()
        if today != self._day:
            self._day, self._trades_today = today, 0

    def _log(self, text: str, level: str = "info") -> None:
        self._events.append({"time": f"{datetime.now():%H:%M:%S}", "level": level, "text": text})

    @staticmethod
    def _side(direction: Direction) -> str:
        return "COMPRA" if direction is Direction.UP else "VENDA"

    @staticmethod
    def _position_view(position: Position | None) -> dict | None:
        if position is None:
            return None
        return {"symbol": position.symbol, "side": TradingRobot._side(position.side), "volume": position.volume,
                "entry": position.entry_price, "current": position.current_price,
                "profit": round(position.profit, 2), "stop": position.stop_price,
                "opened_at": f"{position.opened_at:%H:%M:%S}"}
