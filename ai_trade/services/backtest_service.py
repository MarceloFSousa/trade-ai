from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd

from ..backtest.engine import BacktestEngine
from ..domain import BacktestResult, Prediction
from ..interfaces import AIPredictor, MarketDataProvider, PromptBuilder

ProgressFn = Callable[[int, int, pd.Timestamp, Prediction], None]


@dataclass(frozen=True)
class BacktestParams:
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    bars: int
    step: int
    max_calls: int | None
    point_value: float = 1.0
    volume: float = 1.0
    stop_points: float = 0.0


@dataclass(frozen=True)
class BacktestRun:
    candles: pd.DataFrame   # trecho do histórico que foi analisado (para o gráfico)
    result: BacktestResult
    total_candles: int


class BacktestService:
    """Busca o histórico e roda o BacktestEngine. Usado pela CLI e pela web."""

    def __init__(self, provider: MarketDataProvider, predictor: AIPredictor, prompt_builder: PromptBuilder):
        self._provider = provider
        self._predictor = predictor
        self._prompt_builder = prompt_builder

    def run(self, params: BacktestParams, on_progress: ProgressFn | None = None,
            should_stop: Callable[[], bool] | None = None) -> BacktestRun:
        candles = self._provider.get_history(params.symbol, params.timeframe, params.start, params.end)
        engine = BacktestEngine(self._predictor, self._prompt_builder, bars=params.bars, step=params.step,
                                max_calls=params.max_calls, point_value=params.point_value,
                                volume=params.volume, stop_points=params.stop_points, on_progress=on_progress)
        result = engine.run(params.symbol, params.timeframe, candles, should_stop)

        # mostra só o trecho analisado (janela inicial + candles operados)
        last = result.trades[-1].signal_time if result.trades else candles["time"].iloc[-1]
        shown = candles[candles["time"] <= last].reset_index(drop=True)
        return BacktestRun(shown, result, len(candles))
