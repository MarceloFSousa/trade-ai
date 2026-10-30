from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from ..domain import CANDLE_COLUMNS, Quote
from ..interfaces import MarketDataProvider, QuoteProvider
from . import mt5_session
from .mt5_session import LOCK

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3,
    "M5": mt5.TIMEFRAME_M5, "M10": mt5.TIMEFRAME_M10, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
}


def server_time(seconds: int) -> datetime:
    """O MT5 grava a hora do servidor como se fosse UTC; fromtimestamp() a deslocaria pelo fuso local."""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


class MT5Provider(MarketDataProvider, QuoteProvider):
    """Lê candles e cotações do terminal MetaTrader 5 (precisa estar aberto e logado)."""

    def __init__(self, terminal_path: str | None = None):
        self._terminal_path = terminal_path
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    def connect(self) -> None:
        if not self._connected:
            mt5_session.acquire(self._terminal_path)
            self._connected = True

    def close(self) -> None:
        if self._connected:
            mt5_session.release()
            self._connected = False

    def get_last_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        with LOCK:
            self._prepare(symbol)
            # posição 0 é o candle em formação; a partir de 1 só candles fechados
            rates = mt5.copy_rates_from_pos(symbol, self._timeframe(timeframe), 1, count)
            return self._to_frame(rates, symbol)

    def get_history(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        with LOCK:
            self._prepare(symbol)
            rates = mt5.copy_rates_range(symbol, self._timeframe(timeframe), start, end)
            return self._to_frame(rates, symbol)

    def get_quote(self, symbol: str) -> Quote:
        with LOCK:
            self._prepare(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise RuntimeError(f"Sem cotação para {symbol}: {mt5.last_error()}")
            return Quote(tick.bid, tick.ask, server_time(tick.time))

    def money_per_point(self, symbol: str) -> float:
        with LOCK:
            self._prepare(symbol)
            info = mt5.symbol_info(symbol)
            return info.trade_tick_value / info.trade_tick_size

    def _prepare(self, symbol: str) -> None:
        self.connect()
        if not mt5.symbol_select(symbol, True):
            raise ValueError(f"Símbolo '{symbol}' não encontrado no MetaTrader 5: {mt5.last_error()}")

    @staticmethod
    def _timeframe(timeframe: str) -> int:
        try:
            return TIMEFRAMES[timeframe.upper()]
        except KeyError:
            raise ValueError(f"Timeframe inválido '{timeframe}'. Use um de: {', '.join(TIMEFRAMES)}")

    @staticmethod
    def _to_frame(rates, symbol: str) -> pd.DataFrame:
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Nenhum candle retornado para {symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[CANDLE_COLUMNS].reset_index(drop=True)
