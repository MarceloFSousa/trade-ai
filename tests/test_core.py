"""Testes sem MetaTrader 5 e sem API: usam implementações fake das interfaces."""
from types import SimpleNamespace

import pandas as pd
import pytest

from ai_trade.ai.claude_predictor import ClaudePredictor
from ai_trade.backtest.engine import BacktestEngine
from ai_trade.backtest.metrics import max_drawdown
from ai_trade.charts.plotly_renderer import PlotlyRenderer
from ai_trade.domain import Direction, Prediction
from ai_trade.interfaces import AIPredictor, PromptBuilder
from ai_trade.prompt.file_prompt import FilePromptBuilder


def make_candles(n: int) -> pd.DataFrame:
    # candles alternando alta (+5) e baixa (-5)
    rows = []
    for i in range(n):
        o = 100.0 + i
        c = o + (5 if i % 2 == 0 else -5)
        rows.append({"time": pd.Timestamp("2026-10-01 09:00") + pd.Timedelta(minutes=5 * i),
                     "open": o, "high": max(o, c) + 1, "low": min(o, c) - 1, "close": c, "tick_volume": 10})
    return pd.DataFrame(rows)


class EchoPrompt(PromptBuilder):
    def build(self, symbol, timeframe, candles, bars=None):
        return str(len(candles.tail(bars))) + "|" + str(candles["time"].iloc[-1])


class AlwaysUp(AIPredictor):
    def __init__(self):
        self.prompts = []

    def predict(self, prompt):
        self.prompts.append(prompt)
        return Prediction(Direction.UP, 0.7, "sempre compra")


def test_backtest_entra_na_abertura_e_sai_no_fechamento_sem_look_ahead():
    candles = make_candles(10)
    ai = AlwaysUp()
    result = BacktestEngine(ai, EchoPrompt(), bars=3, step=1, point_value=2.0).run("X", "M5", candles)

    assert result.calls == 7                      # candles 3..9
    assert len(result.trades) == 7
    first = result.trades[0]
    assert first.signal_time == candles.at[3, "time"]
    assert first.entry_price == candles.at[3, "open"] and first.exit_price == candles.at[3, "close"]
    assert first.points == -5                     # candle 3 é de baixa
    # a janela do primeiro sinal termina no candle 2 (anterior ao operado)
    assert ai.prompts[0] == f"3|{candles.at[2, 'time']}"
    assert result.metrics["acertos"] == 3 and result.metrics["erros"] == 4
    assert result.metrics["resultado_$"] == -10.0


def test_stop_encerra_no_preco_do_stop():
    candles = make_candles(5)     # candle 3: abre 103, mínima 97, fecha 98
    result = BacktestEngine(AlwaysUp(), EchoPrompt(), bars=3, stop_points=4).run("X", "M5", candles)
    first, second = result.trades
    assert first.stopped_out and first.exit_price == 99 and first.points == -4
    assert not second.stopped_out and second.exit_price == candles.at[4, "close"]
    assert result.metrics["stops"] == 1


def test_stop_na_venda_olha_a_maxima():
    class AlwaysDown(AlwaysUp):
        def predict(self, prompt):
            return Prediction(Direction.DOWN, 0.7, "vende")

    candles = make_candles(5)     # candle 4: abre 104, máxima 110, fecha 109
    result = BacktestEngine(AlwaysDown(), EchoPrompt(), bars=3, stop_points=4).run("X", "M5", candles)
    assert result.trades[1].stopped_out and result.trades[1].exit_price == 108 and result.trades[1].points == -4


def test_sem_stop_nao_altera_a_saida():
    candles = make_candles(5)
    result = BacktestEngine(AlwaysUp(), EchoPrompt(), bars=3).run("X", "M5", candles)
    assert [t.exit_price for t in result.trades] == list(candles["close"].iloc[3:])
    assert result.metrics["stops"] == 0


def test_backtest_respeita_step_e_max_calls():
    engine = BacktestEngine(AlwaysUp(), EchoPrompt(), bars=5, step=10, max_calls=3)
    assert engine.planned_calls(100) == 3
    result = engine.run("X", "M5", make_candles(100))
    assert result.calls == 3
    assert [t.signal_time for t in result.trades] == list(make_candles(100)["time"].iloc[[5, 15, 25]])


def test_backtest_neutral_e_erro_da_ia_nao_operam():
    class Flaky(AIPredictor):
        n = 0

        def predict(self, prompt):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("timeout")
            return Prediction.neutral("sem vantagem")

    result = BacktestEngine(Flaky(), EchoPrompt(), bars=2).run("X", "M5", make_candles(5))
    assert result.calls == 3 and result.neutrals == 3 and result.trades == []


def test_drawdown():
    assert max_drawdown([10, 5, 20, 8, 12]) == 12


def test_file_prompt_builder(tmp_path):
    f = tmp_path / "p.md"
    f.write_text('Ativo {symbol_or_alias} {timeframe} n={n_bars} json {"x": 1}\n{candles}\n'
                 'ATR {atr} | {ema_relation} | {close_vs_ema20} | {window_high} / {window_low}', encoding="utf-8")
    candles = make_candles(60)
    candles.loc[59, ["open", "close"]] = [171400.0, 171255.0]
    text = FilePromptBuilder(f).build("WIN$N", "M5", candles, bars=3)
    assert text.startswith('Ativo WIN$N M5 n=3 json {"x": 1}')
    lines = text.splitlines()
    assert lines[1] == "idx,open,high,low,close,volume,body_pct,upper_wick_pct,lower_wick_pct,range_atr,vol_ratio"
    assert lines[2].startswith("-3,") and lines[4].startswith("-1,171400,")   # preço exato, sem notação científica
    assert ",171255," in lines[4] and "e+" not in text
    assert "ATR" in lines[5] and "{" not in lines[5]


def test_features_nao_quebram_com_candle_sem_range():
    from ai_trade.prompt.features import enrich
    candles = make_candles(5)
    candles.loc[4, ["open", "high", "low", "close"]] = 100.0
    row = enrich(candles).iloc[-1]
    assert row["body_pct"] == 0 and row["range_atr"] == 0 and row["idx"] == -1


def fake_client(block_input):
    block = SimpleNamespace(type="tool_use", name="submit_prediction", input=block_input)
    response = SimpleNamespace(content=[block], stop_reason="tool_use")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return response

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


def test_claude_predictor_parse():
    client, calls = fake_client({"signal": "down", "confidence": 1.4, "reasoning": "topo duplo"})
    p = ClaudePredictor("claude-haiku-5-5", client=client).predict("prompt")
    assert p == Prediction(Direction.DOWN, 1.0, "topo duplo")
    assert calls[0]["tool_choice"] == {"type": "tool", "name": "submit_prediction"}
    assert "temperature" not in calls[0]


def test_claude_predictor_resposta_invalida_vira_neutral():
    client, _ = fake_client({"signal": "SIDEWAYS"})
    assert ClaudePredictor("m", client=client).predict("x").direction is Direction.NEUTRAL


@pytest.mark.parametrize("direction", list(Direction))
def test_renderers_geram_html(tmp_path, direction):
    candles = make_candles(30)
    r = PlotlyRenderer()
    a = r.render_analysis("X", "M5", candles, Prediction(direction, 0.6, "motivo <b>"), tmp_path / "a.html")
    assert "motivo &lt;b&gt;" in a.read_text(encoding="utf-8")
    result = BacktestEngine(AlwaysUp(), EchoPrompt(), bars=5, step=3).run("X", "M5", candles)
    b = r.render_backtest(candles, result, tmp_path / "b.html")
    assert "Curva de capital" in b.read_text(encoding="utf-8")


def fake_gemini(text=None, error=None):
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return SimpleNamespace(text=text)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


def test_gemini_predictor_parse():
    from ai_trade.ai.gemini_predictor import GeminiPredictor
    client, calls = fake_gemini('{"signal": "up", "confidence": 0.8, "reasoning": "rompimento", "structure": "uptrend", "bullish_factors": ["idx -1"]}')
    p = GeminiPredictor("gemini-3.5-flash-lite", client=client).predict("prompt")
    assert p == Prediction(Direction.UP, 0.8, "rompimento Estrutura: uptrend. Alta: idx -1.")
    assert calls[0]["model"] == "gemini-3.5-flash-lite"
    assert calls[0]["config"].response_mime_type == "application/json"


def test_gemini_resposta_cortada_vira_neutral():
    from ai_trade.ai.gemini_predictor import GeminiPredictor
    client, _ = fake_gemini(None)
    assert GeminiPredictor("m", client=client).predict("x").direction is Direction.NEUTRAL


def test_gemini_cota_esgotada_interrompe_backtest():
    from google.genai import errors
    from ai_trade.ai.gemini_predictor import GeminiPredictor
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client, calls = fake_gemini(error=quota)
    result = BacktestEngine(GeminiPredictor("m", client=client), EchoPrompt(), bars=3).run("X", "M5", make_candles(10))
    assert result.stopped and "Limite" in result.stop_reason
    assert len(calls) == 1 and result.trades == []


def test_factory_registra_gemini(monkeypatch):
    from ai_trade.ai.factory import create_predictor
    from ai_trade.ai.gemini_predictor import GeminiPredictor
    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    assert isinstance(create_predictor({"provider": "gemini", "model": "gemini-3.8-flash"}), GeminiPredictor)
