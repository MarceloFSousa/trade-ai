from ..domain import Direction, Trade


def equity_curve(trades: list[Trade], point_value: float = 1.0, volume: float = 1.0) -> list[float]:
    """Resultado financeiro acumulado após cada trade."""
    total, curve = 0.0, []
    for t in trades:
        total += t.points * point_value * volume
        curve.append(total)
    return curve


def max_drawdown(curve: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def compute_metrics(trades: list[Trade], calls: int, neutrals: int,
                    point_value: float = 1.0, volume: float = 1.0) -> dict:
    wins = [t for t in trades if t.won]
    losses = [t for t in trades if t.points < 0]
    curve = equity_curve(trades, point_value, volume)
    gross_win = sum(t.points for t in wins)
    gross_loss = -sum(t.points for t in losses)
    n = len(trades)
    return {
        "chamadas_ia": calls,
        "neutros": neutrals,
        "trades": n,
        "compras": sum(t.direction is Direction.UP for t in trades),
        "vendas": sum(t.direction is Direction.DOWN for t in trades),
        "acertos": len(wins),
        "erros": len(losses),
        "stops": sum(t.stopped_out for t in trades),
        "taxa_acerto_%": round(100 * len(wins) / n, 2) if n else 0.0,
        "pontos": round(sum(t.points for t in trades), 2),
        "resultado_$": round(curve[-1], 2) if curve else 0.0,
        "media_pontos_trade": round(sum(t.points for t in trades) / n, 2) if n else 0.0,
        "fator_lucro": round(gross_win / gross_loss, 2) if gross_loss else None,
        "drawdown_max_$": round(max_drawdown(curve), 2),
    }
