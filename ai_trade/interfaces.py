"""Abstrações do sistema. Serviços dependem apenas destas classes.

Para trocar a fonte de dados, a IA, o prompt ou o gráfico basta criar uma
nova implementação da interface correspondente e injetá-la em main.py.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import pandas as pd

from .domain import AccountInfo, BacktestResult, ClosedPosition, Direction, Position, Prediction, Quote


class MarketDataProvider(ABC):
    @abstractmethod
    def get_last_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Últimos `count` candles FECHADOS, do mais antigo para o mais recente."""

    @abstractmethod
    def get_history(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Todos os candles entre `start` e `end`."""


class QuoteProvider(ABC):
    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Melhor oferta de compra/venda agora."""

    @abstractmethod
    def money_per_point(self, symbol: str) -> float:
        """Valor em dinheiro de 1 ponto por contrato."""


class OrderExecutor(ABC):
    """Envia e consulta ordens. Só enxerga as posições do próprio robô."""

    @abstractmethod
    def account(self) -> AccountInfo: ...

    @abstractmethod
    def ensure_tradable(self, symbol: str) -> None:
        """Levanta ValueError se o símbolo não aceitar ordens."""

    @abstractmethod
    def position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    def has_foreign_position(self, symbol: str) -> bool:
        """Há posição neste símbolo que não é do robô (manual ou de outro EA)?"""

    @abstractmethod
    def open(self, symbol: str, side: Direction, volume: float, stop_points: float) -> Position: ...

    @abstractmethod
    def close(self, symbol: str) -> ClosedPosition | None:
        """Zera a posição do robô. Devolve None se não havia posição."""

    @abstractmethod
    def realized_today(self, symbol: str) -> float:
        """Resultado em dinheiro das operações do robô fechadas hoje."""


class PromptBuilder(ABC):
    warmup_bars: int = 0    # candles extras, antes da janela, para calcular indicadores

    @abstractmethod
    def build(self, symbol: str, timeframe: str, candles: pd.DataFrame, bars: int | None = None) -> str:
        """Monta o texto enviado à IA. `candles` inclui o warmup; a IA vê os últimos `bars`."""


class AIPredictor(ABC):
    @abstractmethod
    def predict(self, prompt: str) -> Prediction:
        """Envia o prompt para a IA e devolve a previsão do próximo candle."""


class ChartRenderer(ABC):
    @abstractmethod
    def render_analysis(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        prediction: Prediction, path: Path) -> Path:
        """Gráfico dos candles enviados + previsão do próximo."""

    @abstractmethod
    def render_backtest(self, candles: pd.DataFrame, result: BacktestResult, path: Path) -> Path:
        """Gráfico do período com entradas, saídas e curva de capital."""
