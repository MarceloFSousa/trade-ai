import logging
from typing import Callable

import pandas as pd

from ..domain import AIQuotaError, BacktestResult, Direction, Prediction, Trade
from ..interfaces import AIPredictor, PromptBuilder
from .metrics import compute_metrics

log = logging.getLogger(__name__)


class BacktestEngine:
    """Caminha pelo histórico: a cada `step` candles envia os `bars` candles
    anteriores (já fechados) para a IA e opera o candle seguinte, entrando
    na abertura e saindo no fechamento dele."""

    def __init__(self, predictor: AIPredictor, prompt_builder: PromptBuilder,
                 bars: int, step: int = 1, max_calls: int | None = None,
                 point_value: float = 1.0, volume: float = 1.0, stop_points: float = 0.0,
                 on_progress: Callable[[int, int, pd.Timestamp, Prediction], None] | None = None):
        if bars < 1 or step < 1:
            raise ValueError("bars e step devem ser >= 1")
        self._predictor = predictor
        self._prompt_builder = prompt_builder
        self._bars = bars
        self._step = step
        self._max_calls = max_calls
        self._point_value = point_value
        self._volume = volume
        self._stop_points = stop_points     # 0 = sem stop
        self._on_progress = on_progress

    @property
    def _first_index(self) -> int:
        return self._bars + self._prompt_builder.warmup_bars

    def planned_calls(self, total_candles: int) -> int:
        n = len(range(self._first_index, total_candles, self._step))
        return min(n, self._max_calls) if self._max_calls else n

    def run(self, symbol: str, timeframe: str, candles: pd.DataFrame,
            should_stop: Callable[[], bool] | None = None) -> BacktestResult:
        if len(candles) <= self._first_index:
            raise ValueError(f"Histórico com {len(candles)} candles; são necessários mais de {self._first_index} "
                             f"({self._bars} da janela + {self._prompt_builder.warmup_bars} para os indicadores). "
                             "Aumente o período.")

        result = BacktestResult(symbol, timeframe, self._point_value, self._volume)
        planned = self.planned_calls(len(candles))

        for i in range(self._first_index, len(candles), self._step):
            if self._max_calls and result.calls >= self._max_calls:
                break
            if should_stop and should_stop():
                result.stopped, result.stop_reason = True, "Cancelado pelo usuário."
                break
            window = candles.iloc[i - self._first_index:i].reset_index(drop=True)  # sem look-ahead
            prompt = self._prompt_builder.build(symbol, timeframe, window, self._bars)
            try:
                prediction = self._predictor.predict(prompt)
            except AIQuotaError as exc:  # cota esgotada: as próximas chamadas também falhariam
                log.error("Backtest interrompido: %s", exc)
                result.stopped, result.stop_reason = True, str(exc)
                break
            except Exception as exc:  # um erro de API não deve derrubar o backtest inteiro
                log.error("Erro na IA no candle %s: %s", candles.at[i, "time"], exc)
                prediction = Prediction.neutral(f"Erro: {exc}")
            result.calls += 1

            target = candles.iloc[i]
            if self._on_progress:
                self._on_progress(result.calls, planned, target["time"], prediction)

            if prediction.direction is Direction.NEUTRAL:
                result.neutrals += 1
                continue

            sign = 1 if prediction.direction is Direction.UP else -1
            entry, exit_ = float(target["open"]), float(target["close"])
            stopped_out = False
            if self._stop_points > 0:      # mesma regra do robô: pior preço do candle atingiu o stop
                adverse = float(target["low"] if sign > 0 else target["high"])
                if sign * (adverse - entry) <= -self._stop_points:
                    exit_, stopped_out = entry - sign * self._stop_points, True
            result.trades.append(Trade(
                signal_time=target["time"],
                direction=prediction.direction,
                entry_price=entry,
                exit_price=exit_,
                points=sign * (exit_ - entry) + 0.0,  # + 0.0 evita "-0" em venda empatada
                confidence=prediction.confidence,
                reasoning=prediction.reasoning,
                stopped_out=stopped_out,
            ))

        result.metrics = compute_metrics(result.trades, result.calls, result.neutrals,
                                         result.point_value, result.volume)
        return result
