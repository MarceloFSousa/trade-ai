"""Objetos de domínio, independentes de MT5 ou de qualquer IA.

Candles circulam como pandas.DataFrame com as colunas de CANDLE_COLUMNS,
indexados de 0..n-1 e ordenados do mais antigo para o mais recente.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

CANDLE_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume"]
TIMEFRAMES = ["M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]


class AIQuotaError(Exception):
    """A IA recusou por limite de uso (ex.: cota diária do plano gratuito). Não adianta continuar."""


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class Prediction:
    direction: Direction
    confidence: float
    reasoning: str

    @classmethod
    def neutral(cls, reason: str) -> "Prediction":
        return cls(Direction.NEUTRAL, 0.0, reason)


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    time: datetime          # horário do último negócio/cotação


@dataclass(frozen=True)
class Position:
    """Posição aberta pelo robô. `profit` é em dinheiro, não em pontos."""
    symbol: str
    side: Direction         # UP = comprado, DOWN = vendido
    volume: float
    entry_price: float
    current_price: float
    profit: float
    stop_price: float | None
    opened_at: datetime


@dataclass(frozen=True)
class AccountInfo:
    mode: str               # PAPER, DEMO, CONTEST ou REAL
    balance: float
    equity: float
    currency: str
    trading_enabled: bool   # conta e botão Algo Trading permitem operar
    detail: str = ""


@dataclass(frozen=True)
class ClosedPosition:
    exit_price: float
    profit: float


@dataclass(frozen=True)
class Trade:
    signal_time: datetime   # abertura do candle operado
    direction: Direction
    entry_price: float      # open do candle seguinte à janela
    exit_price: float       # close do mesmo candle, ou o preço do stop
    points: float           # resultado em pontos, já com sinal
    confidence: float
    reasoning: str
    stopped_out: bool = False

    @property
    def won(self) -> bool:
        return self.points > 0


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    point_value: float = 1.0
    volume: float = 1.0
    trades: list[Trade] = field(default_factory=list)
    calls: int = 0
    neutrals: int = 0
    stopped: bool = False   # interrompido antes do fim (cancelado ou cota da IA esgotada)
    stop_reason: str | None = None
    metrics: dict = field(default_factory=dict)
