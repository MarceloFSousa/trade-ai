import threading
from dataclasses import replace
from datetime import datetime

from ..domain import AccountInfo, ClosedPosition, Direction, Position
from ..interfaces import OrderExecutor, QuoteProvider


class PaperExecutor(OrderExecutor):
    """Simula ordens sobre cotações reais: nenhuma ordem chega ao MetaTrader.

    O stop é conferido a cada consulta de posição, com execução no preço do stop.
    """

    def __init__(self, quotes: QuoteProvider, balance: float = 100_000.0, currency: str = "BRL"):
        self._quotes = quotes
        self._balance = balance
        self._currency = currency
        self._position: Position | None = None
        self._realized = 0.0
        self._day = datetime.now().date()
        self._lock = threading.RLock()

    def account(self) -> AccountInfo:
        with self._lock:
            position = self._current()
            floating = position.profit if position else 0.0
            return AccountInfo("PAPER", self._balance, self._balance + floating, self._currency, True)

    def ensure_tradable(self, symbol: str) -> None:
        self._quotes.get_quote(symbol)   # só precisa ter cotação

    def position(self, symbol: str) -> Position | None:
        with self._lock:
            position = self._current()
            return position if position and position.symbol == symbol else None

    def has_foreign_position(self, symbol: str) -> bool:
        return False

    def open(self, symbol: str, side: Direction, volume: float, stop_points: float) -> Position:
        with self._lock:
            if self._position:
                raise RuntimeError("Já existe posição aberta.")
            quote = self._quotes.get_quote(symbol)
            price = quote.ask if side is Direction.UP else quote.bid
            stop = None
            if stop_points > 0:
                stop = price - stop_points if side is Direction.UP else price + stop_points
            self._position = Position(symbol, side, volume, price, price, 0.0, stop, datetime.now())
            return self._position

    def close(self, symbol: str) -> ClosedPosition | None:
        with self._lock:
            position = self.position(symbol)
            return self._settle(position, position.current_price) if position else None

    def realized_today(self, symbol: str) -> float:
        with self._lock:
            self._current()
            if datetime.now().date() != self._day:
                self._day, self._realized = datetime.now().date(), 0.0
            return self._realized

    def _current(self) -> Position | None:
        """Marca a posição a mercado e executa o stop se foi atingido."""
        position = self._position
        if position is None:
            return None
        quote = self._quotes.get_quote(position.symbol)
        long = position.side is Direction.UP
        price = quote.bid if long else quote.ask          # preço que zeraria a posição
        stop = position.stop_price
        if stop is not None and (price <= stop if long else price >= stop):
            self._settle(position, stop)
            return None
        self._position = replace(position, current_price=price, profit=self._profit(position, price))
        return self._position

    def _profit(self, position: Position, price: float) -> float:
        sign = 1 if position.side is Direction.UP else -1
        return sign * (price - position.entry_price) * position.volume * self._quotes.money_per_point(position.symbol)

    def _settle(self, position: Position, price: float) -> ClosedPosition:
        profit = self._profit(position, price)
        self._balance += profit
        self._realized += profit
        self._position = None
        return ClosedPosition(price, profit)
