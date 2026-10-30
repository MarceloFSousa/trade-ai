"""Indicadores calculados sobre os candles antes de enviá-los à IA."""
import pandas as pd

ATR_PERIOD = 14
VOLUME_PERIOD = 20
EMA_FAST, EMA_SLOW = 20, 50


def atr(candles: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    previous_close = candles["close"].shift()
    true_range = pd.concat([
        candles["high"] - candles["low"],
        (candles["high"] - previous_close).abs(),
        (candles["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()   # média de Wilder


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def enrich(history: pd.DataFrame) -> pd.DataFrame:
    """Devolve idx (-n..-1) e as colunas relativas descritas no prompt."""
    out = pd.DataFrame({"idx": range(-len(history), 0)}, index=history.index)
    for column in ("open", "high", "low", "close"):
        out[column] = history[column]
    out["volume"] = history["tick_volume"]

    candle_range = history["high"] - history["low"]
    safe_range = candle_range.where(candle_range > 0)        # candle sem range vira 0, não divisão por zero
    out["body_pct"] = ((history["close"] - history["open"]).abs() / safe_range).fillna(0)
    out["upper_wick_pct"] = ((history["high"] - history[["open", "close"]].max(axis=1)) / safe_range).fillna(0)
    out["lower_wick_pct"] = ((history[["open", "close"]].min(axis=1) - history["low"]) / safe_range).fillna(0)
    out["range_atr"] = candle_range / atr(history)
    out["vol_ratio"] = history["tick_volume"] / history["tick_volume"].rolling(VOLUME_PERIOD, min_periods=1).mean()
    return out


def summary(history: pd.DataFrame, bars: int) -> dict:
    """Valores do bloco FEATURES, sempre relativos ao último candle fechado."""
    close = history["close"]
    window = history.tail(bars)
    current_atr = float(atr(history).iloc[-1])
    fast, slow = float(ema(close, EMA_FAST).iloc[-1]), float(ema(close, EMA_SLOW).iloc[-1])

    def in_atr(distance: float) -> str:
        return f"{abs(distance) / current_atr:.1f} ATR" if current_atr else "0.0 ATR"

    last_close = float(close.iloc[-1])
    return {
        "atr": f"{current_atr:.1f}",
        "ema_relation": f"EMA{EMA_FAST} {'acima' if fast >= slow else 'abaixo'} da EMA{EMA_SLOW}, "
                        f"distância {in_atr(fast - slow)}",
        "close_vs_ema20": f"{in_atr(last_close - fast)} {'acima' if last_close >= fast else 'abaixo'}",
        "window_high": f"{window['high'].max():g}",
        "window_low": f"{window['low'].min():g}",
    }
