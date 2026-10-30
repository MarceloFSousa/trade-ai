from dataclasses import dataclass

import pandas as pd

from ..domain import Prediction
from ..interfaces import AIPredictor, MarketDataProvider, PromptBuilder


@dataclass(frozen=True)
class Analysis:
    candles: pd.DataFrame
    prompt: str
    prediction: Prediction


class Analyzer:
    """Análise ao vivo: últimos N candles fechados -> prompt -> IA."""

    def __init__(self, provider: MarketDataProvider, predictor: AIPredictor, prompt_builder: PromptBuilder):
        self._provider = provider
        self._predictor = predictor
        self._prompt_builder = prompt_builder

    def analyze(self, symbol: str, timeframe: str, bars: int) -> Analysis:
        history = self._provider.get_last_bars(symbol, timeframe, bars + self._prompt_builder.warmup_bars)
        prompt = self._prompt_builder.build(symbol, timeframe, history, bars)
        candles = history.tail(bars).reset_index(drop=True)
        return Analysis(candles, prompt, self._predictor.predict(prompt))
