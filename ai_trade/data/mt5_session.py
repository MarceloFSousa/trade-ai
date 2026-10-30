"""Conexão única com o MetaTrader 5, compartilhada por todo o processo.

A biblioteca MetaTrader5 tem um estado global e não é thread-safe. Robô e
backtest rodam em threads diferentes, então toda chamada passa por LOCK e a
conexão só é encerrada quando o último usuário sair: assim um backtest que
termina não derruba o robô.
"""
import threading

import MetaTrader5 as mt5

LOCK = threading.RLock()
_users = 0


def acquire(terminal_path: str | None = None) -> None:
    global _users
    with LOCK:
        if _users == 0:
            ok = mt5.initialize(terminal_path) if terminal_path else mt5.initialize()
            if not ok:
                raise RuntimeError(f"Falha ao conectar no MetaTrader 5: {mt5.last_error()}")
        _users += 1


def release() -> None:
    global _users
    with LOCK:
        _users -= 1
        if _users == 0:
            mt5.shutdown()
