import html
import textwrap
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..domain import BacktestResult, Direction, Prediction
from ..interfaces import ChartRenderer
from ..backtest.metrics import equity_curve

UP_COLOR, DOWN_COLOR, NEUTRAL_COLOR = "#26a69a", "#ef5350", "#9e9e9e"
TIME_FMT = "%Y-%m-%d %H:%M"


def _labels(times: pd.Series) -> list[str]:
    # eixo categórico: sem buracos de noite/fim de semana
    return pd.to_datetime(times).dt.strftime(TIME_FMT).tolist()


def _wrap(text: str, width: int = 80) -> str:
    return "<br>".join(textwrap.wrap(html.escape(text), width))


def _candles(df: pd.DataFrame, name: str = "Candles") -> go.Candlestick:
    return go.Candlestick(
        x=_labels(df["time"]), open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=name, increasing_line_color=UP_COLOR, decreasing_line_color=DOWN_COLOR,
    )


def _page(title: str, body: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; margin: 16px; background:#111; color:#ddd; }}
 h1 {{ font-size: 20px; }} .box {{ background:#1c1c1c; padding:12px 16px; border-radius:8px; margin:12px 0; }}
 table {{ border-collapse: collapse; font-size: 13px; }} td, th {{ padding: 4px 10px; border-bottom: 1px solid #333; text-align:left; }}
 .up {{ color:{UP_COLOR}; }} .down {{ color:{DOWN_COLOR}; }} .neutral {{ color:{NEUTRAL_COLOR}; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
{body}
</body></html>""", encoding="utf-8")
    return path


class PlotlyRenderer(ChartRenderer):
    def render_analysis(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        prediction: Prediction, path: Path) -> Path:
        fig = go.Figure(_candles(candles))
        labels = _labels(candles["time"])
        next_label = "PRÓXIMO"
        last = candles.iloc[-1]
        span = float(candles["high"].max() - candles["low"].min()) or 1.0

        color = {Direction.UP: UP_COLOR, Direction.DOWN: DOWN_COLOR}.get(prediction.direction, NEUTRAL_COLOR)
        arrow = {Direction.UP: -1, Direction.DOWN: 1}.get(prediction.direction, 0)
        fig.add_annotation(
            x=next_label, y=float(last["close"]),
            text=f"<b>{prediction.direction.value}</b> {prediction.confidence:.0%}",
            showarrow=True, arrowhead=2, arrowsize=1.5, arrowwidth=3, arrowcolor=color,
            ax=0, ay=60 * arrow if arrow else -40, font=dict(color=color, size=16),
        )
        fig.update_layout(
            template="plotly_dark", height=650, xaxis_rangeslider_visible=False,
            title=f"{symbol} {timeframe} — {len(candles)} candles enviados à IA",
            xaxis=dict(type="category", categoryorder="array", categoryarray=labels + [next_label], nticks=12),
            yaxis=dict(range=[candles["low"].min() - span * 0.1, candles["high"].max() + span * 0.1]),
            showlegend=False,
        )
        cls = prediction.direction.value.lower()
        body = fig.to_html(full_html=False, include_plotlyjs="cdn") + f"""
<div class="box"><b>Previsão:</b> <span class="{cls}">{prediction.direction.value}</span>
 — confiança {prediction.confidence:.0%}<br><br>{html.escape(prediction.reasoning)}</div>"""
        return _page(f"Análise {symbol} {timeframe}", body, path)

    def render_backtest(self, candles: pd.DataFrame, result: BacktestResult, path: Path) -> Path:
        fig = self.backtest_figure(candles, result)
        trades = result.trades
        metrics = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in result.metrics.items())
        rows = "".join(
            f"<tr><td>{t.signal_time:%Y-%m-%d %H:%M}</td>"
            f"<td class='{t.direction.value.lower()}'>{'COMPRA' if t.direction is Direction.UP else 'VENDA'}</td>"
            f"<td>{t.confidence:.0%}</td><td>{t.entry_price:g}</td><td>{t.exit_price:g}</td>"
            f"<td class='{'up' if t.won else 'down'}'>{t.points:+g}</td><td>{html.escape(t.reasoning)}</td></tr>"
            for t in trades
        )
        body = (f"<div class='box'><table>{metrics}</table></div>"
                + fig.to_html(full_html=False, include_plotlyjs="cdn")
                + "<div class='box'><table><tr><th>Candle</th><th>Lado</th><th>Conf.</th><th>Entrada</th>"
                  f"<th>Saída</th><th>Pontos</th><th>Raciocínio</th></tr>{rows}</table></div>")
        return _page(f"Backtest {result.symbol} {result.timeframe}", body, path)

    def backtest_figure(self, candles: pd.DataFrame, result: BacktestResult) -> go.Figure:
        """Candles + entradas/saídas + curva de capital (usado no HTML e na web)."""
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28],
                            vertical_spacing=0.03, subplot_titles=("Preço", "Curva de capital"))
        fig.add_trace(_candles(candles), row=1, col=1)

        trades = result.trades
        if trades:
            x = [t.signal_time.strftime(TIME_FMT) for t in trades]
            hover = [
                f"<b>{'COMPRA' if t.direction is Direction.UP else 'VENDA'}</b> {t.confidence:.0%}<br>"
                f"Entrada {t.entry_price:g} → Saída {t.exit_price:g}<br>"
                f"Resultado: {t.points:+g} pts<br><br>{_wrap(t.reasoning)}"
                for t in trades
            ]
            for direction, symbol, color, name in (
                (Direction.UP, "triangle-up", UP_COLOR, "Compra"),
                (Direction.DOWN, "triangle-down", DOWN_COLOR, "Venda"),
            ):
                idx = [i for i, t in enumerate(trades) if t.direction is direction]
                fig.add_trace(go.Scatter(
                    x=[x[i] for i in idx], y=[trades[i].entry_price for i in idx], mode="markers",
                    name=f"Entrada {name}", marker=dict(symbol=symbol, size=13, color=color,
                                                        line=dict(width=1, color="white")),
                    hovertext=[hover[i] for i in idx], hoverinfo="text",
                ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=x, y=[t.exit_price for t in trades], mode="markers", name="Saída",
                marker=dict(symbol="x", size=10, color=[UP_COLOR if t.won else DOWN_COLOR for t in trades],
                            line=dict(width=1, color="white")),
                hovertext=hover, hoverinfo="text",
            ), row=1, col=1)

            curve = equity_curve(trades, result.point_value, result.volume)
            fig.add_trace(go.Scatter(x=x, y=curve, mode="lines+markers", name="Capital",
                                     line=dict(color="#42a5f5")), row=2, col=1)

        labels = _labels(candles["time"])
        fig.update_layout(template="plotly_dark", height=850, xaxis_rangeslider_visible=False,
                          title=f"Backtest {result.symbol} {result.timeframe}", hovermode="closest")
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=labels, nticks=12)
        return fig
