"""Robô, simulação e WebSocket com fakes: nada aqui fala com MT5 ou com IA de verdade."""
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_trade.charts.plotly_renderer import PlotlyRenderer
from ai_trade.domain import AccountInfo, Direction, Prediction, Quote
from ai_trade.execution.paper import PaperExecutor
from ai_trade.interfaces import AIPredictor, MarketDataProvider, PromptBuilder, QuoteProvider
from ai_trade.robot.robot import RobotParams, RobotSession, TradingRobot
from ai_trade.web.app import create_app
from tests.test_web import CFG


class Market(MarketDataProvider, QuoteProvider):
    def __init__(self):
        self.price = 1000.0
        self.candle = 0

    def get_last_bars(self, symbol, timeframe, count):
        times = pd.date_range("2026-10-30 10:00", periods=self.candle + count, freq="5min")[-count:]
        return pd.DataFrame({"time": times, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1})

    def get_history(self, symbol, timeframe, start, end):
        raise NotImplementedError

    def get_quote(self, symbol):
        return Quote(self.price, self.price + 5, datetime.now())

    def money_per_point(self, symbol):
        return 0.2


class Prompt(PromptBuilder):
    def build(self, symbol, timeframe, candles, bars=None):
        return "prompt"


class ScriptedAI(AIPredictor):
    def __init__(self, *predictions):
        self.predictions = list(predictions)
        self.calls = 0

    def predict(self, prompt):
        self.calls += 1
        return self.predictions.pop(0) if self.predictions else Prediction.neutral("nada")


UP = Prediction(Direction.UP, 0.8, "alta")
DOWN = Prediction(Direction.DOWN, 0.8, "baixa")


def make_robot(ai=None, executor=None, allow_real=False, **params):
    market = Market()
    ai = ai or ScriptedAI()
    executor = executor or PaperExecutor(market)
    session = RobotSession(market, market, executor, ai, Prompt())
    defaults = RobotParams("WINZ26", "M5", bars=3, **params)
    robot = TradingRobot(defaults, lambda mode: session, allow_real, poll_seconds=3600)  # o ciclo roda à mão
    return robot, market, ai, executor


def next_candle(robot, market):
    market.candle += 1
    robot._tick(robot._stop_event)


def running(robot, market):
    robot.start()
    robot._tick(robot._stop_event)       # registra o candle atual, sem operar
    return robot


def test_nao_opera_no_candle_em_que_foi_ligado():
    robot, market, ai, _ = make_robot(ScriptedAI(UP))
    running(robot, market)
    assert ai.calls == 0 and robot.snapshot()["position"] is None


def test_compra_e_zera_no_candle_seguinte():
    robot, market, ai, ex = make_robot(ScriptedAI(UP))
    running(robot, market)
    next_candle(robot, market)
    pos = robot.snapshot()["position"]
    assert pos["side"] == "COMPRA" and pos["entry"] == 1005 and pos["stop"] == 705

    market.price = 1100
    next_candle(robot, market)
    snap = robot.snapshot()
    assert snap["position"] is None and snap["trades"][0]["profit"] == pytest.approx(19.0)
    assert snap["pnl_today"] == pytest.approx(19.0) and snap["trades_today"] == 1


def test_confianca_baixa_e_neutral_nao_operam():
    robot, market, _, _ = make_robot(ScriptedAI(Prediction(Direction.UP, 0.3, "fraco"), Prediction.neutral("x")))
    running(robot, market)
    next_candle(robot, market)
    next_candle(robot, market)
    assert robot.snapshot()["trades_today"] == 0


def test_pausa_nao_abre_mas_zera_no_fim_do_candle():
    robot, market, ai, _ = make_robot(ScriptedAI(UP, UP))
    running(robot, market)
    next_candle(robot, market)
    robot.pause()
    next_candle(robot, market)
    snap = robot.snapshot()
    assert snap["position"] is None and ai.calls == 1 and snap["state"] == "paused"
    robot.resume()
    next_candle(robot, market)
    assert robot.snapshot()["position"]["side"] == "COMPRA"


def test_zerar_fecha_e_pausa():
    robot, market, _, _ = make_robot(ScriptedAI(DOWN))
    running(robot, market)
    next_candle(robot, market)
    robot.flatten()
    snap = robot.snapshot()
    assert snap["position"] is None and snap["state"] == "paused" and len(snap["trades"]) == 1


def test_desligar_zera_a_posicao():
    robot, market, _, ex = make_robot(ScriptedAI(UP))
    running(robot, market)
    next_candle(robot, market)
    robot.stop()
    assert robot.snapshot()["state"] == "stopped" and ex.position("WINZ26") is None


def test_stop_atingido_e_detectado():
    robot, market, _, _ = make_robot(ScriptedAI(UP), stop_points=50)
    running(robot, market)
    next_candle(robot, market)
    market.price = 900
    robot._tick(robot._stop_event)
    snap = robot.snapshot()
    assert snap["position"] is None and snap["pnl_today"] == pytest.approx(-10.0)
    assert "fora do robô" in snap["events"][-1]["text"]


def test_perda_maxima_pausa_o_robo():
    robot, market, ai, _ = make_robot(ScriptedAI(UP, UP), max_daily_loss=10)
    running(robot, market)
    next_candle(robot, market)
    market.price = 800
    next_candle(robot, market)
    snap = robot.snapshot()
    assert snap["state"] == "paused" and "perda máxima" in snap["reason"] and ai.calls == 1


def test_limite_de_trades_pausa():
    robot, market, ai, _ = make_robot(ScriptedAI(UP, UP), max_trades_per_day=1)
    running(robot, market)
    next_candle(robot, market)
    next_candle(robot, market)
    assert robot.snapshot()["state"] == "paused" and ai.calls == 1


def test_sinal_e_descartado_se_pausar_enquanto_a_ia_responde():
    robot = None

    class PauseDuring(AIPredictor):
        def predict(self, prompt):
            robot.pause()
            return UP

    robot, market, _, _ = make_robot(PauseDuring())
    running(robot, market)
    next_candle(robot, market)
    assert robot.snapshot()["position"] is None


def test_sinal_atrasado_e_descartado():
    robot, market, _, _ = make_robot(ScriptedAI(UP), max_signal_delay=0.000001)
    running(robot, market)
    next_candle(robot, market)
    snap = robot.snapshot()
    assert snap["position"] is None and "levou" in snap["events"][-1]["text"]


def test_conta_real_bloqueada_por_padrao():
    class RealAccount(PaperExecutor):
        def account(self):
            return AccountInfo("REAL", 1, 1, "BRL", True)

    market = Market()
    robot, _, _, _ = make_robot(executor=RealAccount(market))
    with pytest.raises(ValueError, match="REAL"):
        robot.start()
    assert robot.snapshot()["state"] == "stopped"


def test_posicao_de_outra_origem_impede_entrada():
    class Foreign(PaperExecutor):
        def has_foreign_position(self, symbol):
            return True

    market = Market()
    robot, market, ai, _ = make_robot(ScriptedAI(UP), executor=Foreign(market))
    running(robot, market)
    next_candle(robot, market)
    assert ai.calls == 0


def test_parametros():
    robot, market, _, _ = make_robot()
    robot.update_params({"min_confidence": 0.9})
    assert robot.snapshot()["params"]["min_confidence"] == 0.9
    with pytest.raises(ValueError):
        robot.update_params({"stop_points": 0})
    with pytest.raises(ValueError):
        robot.update_params({"nao_existe": 1})
    running(robot, market)
    with pytest.raises(ValueError, match="desligado"):
        robot.update_params({"symbol": "WDOZ26"})
    robot.update_params({"volume": 2})
    assert robot.snapshot()["params"]["volume"] == 2


def make_client():
    robot, market, ai, _ = make_robot(ScriptedAI(UP))
    app = create_app(CFG, lambda *a: None, PlotlyRenderer(), robot)
    return TestClient(app), robot, market


def test_websocket_envia_estado_e_recebe_comandos():
    client, robot, _ = make_client()
    with client.websocket_connect("/ws/robot") as ws:
        assert ws.receive_json()["state"] == "stopped"
        ws.send_json({"action": "start"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "ack":
                break
        assert msg["ok"] and robot.snapshot()["state"] == "running"

        ws.send_json({"action": "pause"})
        ws.send_json({"action": "acao_invalida"})
        acks = []
        while len(acks) < 2:
            msg = ws.receive_json()
            if msg["type"] == "ack":
                acks.append(msg)
        assert acks[0]["ok"] and not acks[1]["ok"]
        assert robot.snapshot()["state"] == "paused"
        ws.send_json({"action": "stop"})
    robot.stop()


def test_websocket_recusa_outra_origem():
    client, robot, _ = make_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/robot", headers={"origin": "https://site-malicioso.example"}):
            pass
    assert robot.snapshot()["state"] == "stopped"
