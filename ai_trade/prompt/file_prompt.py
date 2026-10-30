from pathlib import Path

import pandas as pd

from ..interfaces import PromptBuilder
from .features import enrich, summary

PRICE_COLUMNS = ["open", "high", "low", "close"]
RATIO_COLUMNS = ["body_pct", "upper_wick_pct", "lower_wick_pct", "range_atr", "vol_ratio"]


class FilePromptBuilder(PromptBuilder):
    """Usa um arquivo de texto como template.

    Placeholders aceitos: {symbol_or_alias}, {timeframe}, {n_bars}, {candles}, {atr},
    {ema_relation}, {close_vs_ema20}, {window_high}, {window_low}.
    O arquivo é relido a cada chamada, então pode ser editado sem reiniciar.
    """

    warmup_bars = 100       # histórico extra para ATR, EMA50 e média de volume estabilizarem

    def __init__(self, path: str | Path):
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(f"Arquivo de prompt não encontrado: {self._path}")

    def build(self, symbol: str, timeframe: str, candles: pd.DataFrame, bars: int | None = None) -> str:
        bars = bars or len(candles)
        template = self._path.read_text(encoding="utf-8")
        values = {
            "symbol_or_alias": symbol,
            "timeframe": timeframe,
            "n_bars": min(bars, len(candles)),
            "candles": self._format(candles, bars),
            **summary(candles, bars),
        }
        # replace em vez de str.format: o template tem chaves no JSON de exemplo
        for key, value in values.items():
            template = template.replace("{" + key + "}", str(value))
        return template

    @staticmethod
    def _format(candles: pd.DataFrame, bars: int) -> str:
        df = enrich(candles).tail(bars).copy()
        df["idx"] = range(-len(df), 0)
        df[RATIO_COLUMNS] = df[RATIO_COLUMNS].round(2)
        for column in PRICE_COLUMNS:         # preço exato: arredondar aqui falsearia níveis e rompimentos
            df[column] = df[column].map(lambda v: f"{v:.10g}")
        return df.to_csv(index=False, lineterminator="\n").strip()
