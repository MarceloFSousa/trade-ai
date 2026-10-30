from datetime import datetime

import MetaTrader5 as mt5

from ..data.mt5_provider import server_time
from ..data.mt5_session import LOCK
from ..domain import AccountInfo, ClosedPosition, Direction, Position
from ..interfaces import OrderExecutor

MAGIC = 770077      # identifica as ordens deste robô; posições com outro magic nunca são tocadas
COMMENT = "ai-trade"
DEVIATION_POINTS = 20
FILLING_FOK_FLAG, FILLING_IOC_FLAG = 1, 2   # bits de symbol_info.filling_mode (a lib Python não os expõe)
ACCOUNT_MODES = {
    mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO",
    mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",
    mt5.ACCOUNT_TRADE_MODE_REAL: "REAL",
}


class MT5Executor(OrderExecutor):
    """Ordens a mercado no terminal MetaTrader 5, com stop na própria ordem."""

    def account(self) -> AccountInfo:
        with LOCK:
            info = mt5.account_info()
            terminal = mt5.terminal_info()
        if info is None or terminal is None:
            raise RuntimeError(f"Sem dados da conta: {mt5.last_error()}")
        enabled = bool(info.trade_allowed and info.trade_expert and terminal.trade_allowed)
        detail = "" if enabled else "Ative o botão Algo Trading no MetaTrader 5."
        return AccountInfo(ACCOUNT_MODES.get(info.trade_mode, "REAL"), info.balance, info.equity,
                           info.currency, enabled, detail)

    def ensure_tradable(self, symbol: str) -> None:
        with LOCK:
            if not mt5.symbol_select(symbol, True):
                raise ValueError(f"Símbolo '{symbol}' não encontrado no MetaTrader 5.")
            info = mt5.symbol_info(symbol)
        if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            raise ValueError(f"'{symbol}' não aceita ordens (série contínua ou contrato vencido). "
                             "Use o contrato vigente, ex.: WINZ26.")

    def position(self, symbol: str) -> Position | None:
        raw = self._own(symbol)
        if raw is None:
            return None
        side = Direction.UP if raw.type == mt5.POSITION_TYPE_BUY else Direction.DOWN
        return Position(symbol, side, raw.volume, raw.price_open, raw.price_current, raw.profit,
                        raw.sl or None, server_time(raw.time))

    def has_foreign_position(self, symbol: str) -> bool:
        with LOCK:
            return any(p.magic != MAGIC for p in mt5.positions_get(symbol=symbol) or [])

    def open(self, symbol: str, side: Direction, volume: float, stop_points: float) -> Position:
        with LOCK:
            self._send(self.open_request(symbol, side, volume, stop_points))
            position = self.position(symbol)
        if position is None:
            raise RuntimeError("Ordem aceita, mas a posição não apareceu no terminal.")
        return position

    def open_request(self, symbol: str, side: Direction, volume: float, stop_points: float) -> dict:
        """Monta a ordem sem enviar (também usado para validar com mt5.order_check)."""
        with LOCK:
            tick = mt5.symbol_info_tick(symbol)
            info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            raise RuntimeError(f"Sem cotação para {symbol}: {mt5.last_error()}")
        long = side is Direction.UP
        price = tick.ask if long else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if long else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC,
            "comment": COMMENT,
            "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": self._filling(info),
        }
        if stop_points > 0:
            raw = price - stop_points if long else price + stop_points
            request["sl"] = round(raw / info.trade_tick_size) * info.trade_tick_size
        return request

    def close(self, symbol: str) -> ClosedPosition | None:
        with LOCK:
            raw = self._own(symbol)
            if raw is None:
                return None
            tick = mt5.symbol_info_tick(symbol)
            info = mt5.symbol_info(symbol)
            long = raw.type == mt5.POSITION_TYPE_BUY
            result = self._send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": raw.volume,
                "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY,
                "position": raw.ticket,        # zera exatamente esta posição, inclusive em conta hedging
                "price": tick.bid if long else tick.ask,
                "deviation": DEVIATION_POINTS,
                "magic": MAGIC,
                "comment": COMMENT,
                "type_time": mt5.ORDER_TIME_DAY,
                "type_filling": self._filling(info),
            })
            return ClosedPosition(result.price, raw.profit)

    def realized_today(self, symbol: str) -> float:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        with LOCK:
            deals = mt5.history_deals_get(start, datetime.now(), group=symbol) or []
        return sum(d.profit + d.commission + d.fee for d in deals if d.magic == MAGIC)

    @staticmethod
    def _own(symbol: str):
        with LOCK:
            return next((p for p in mt5.positions_get(symbol=symbol) or [] if p.magic == MAGIC), None)

    @staticmethod
    def _filling(info) -> int:
        if info.filling_mode & FILLING_IOC_FLAG:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & FILLING_FOK_FLAG:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    @staticmethod
    def _send(request: dict):
        with LOCK:
            result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"Ordem não enviada: {mt5.last_error()}")
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
            raise RuntimeError(f"Ordem recusada ({result.retcode}): {result.comment}")
        return result
