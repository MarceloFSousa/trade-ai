"""API web e BacktestService com implementações fake (sem MT5 e sem API)."""
import threading
import time

from fastapi.testclient import TestClient

from ai_trade.charts.plotly_renderer import PlotlyRenderer
from ai_trade.interfaces import MarketDataProvider
from ai_trade.services.backtest_service import BacktestService
from ai_trade.web.app import create_app
from tests.test_core import AlwaysUp, EchoPrompt, make_candles

CFG = {
    "symbol": "WIN$N", "timeframe": "M5", "bars": 3,
    "ai": {"model": "fake"},
    "backtest": {"start": "2026-10-01", "end": "2026-10-02", "step": 1, "max_calls": 5,
                 "point_value": 1.0, "volume": 1},
}
BODY = {"symbol": "WIN$N", "timeframe": "m5", "start": "2026-10-01", "end": "2026-10-01",
        "bars": 3, "step": 1, "max_calls": 4}


class FakeProvider(MarketDataProvider):
    def __init__(self):
        self.calls = []

    def get_last_bars(self, symbol, timeframe, count):
        return make_candles(count)

    def get_history(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return make_candles(20)


def make_client(run_fn=None):
    provider = FakeProvider()

    def default_run(params, on_progress, should_stop):
        return BacktestService(provider, AlwaysUp(), EchoPrompt()).run(params, on_progress, should_stop)

    return TestClient(create_app(CFG, run_fn or default_run, PlotlyRenderer())), provider


def wait(client, job_id, statuses=("done", "cancelled", "error")):
    for _ in range(100):
        job = client.get(f"/api/backtests/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job não terminou: {job}")


def test_defaults_e_pagina():
    client, _ = make_client()
    d = client.get("/api/defaults").json()
    assert d["symbol"] == "WIN$N" and "M5" in d["timeframes"] and d["max_calls"] == 5
    assert "Rodar backtest" in client.get("/").text
    assert client.get("/plotly.min.js").status_code == 200


def test_backtest_pelo_front_end_usa_parametros_do_formulario():
    client, provider = make_client()
    res = client.post("/api/backtests", json=BODY)
    assert res.status_code == 202
    job = wait(client, res.json()["id"])

    assert job["status"] == "done", job["error"]
    symbol, timeframe, start, end = provider.calls[0]
    assert (symbol, timeframe) == ("WIN$N", "M5")
    assert f"{start:%Y-%m-%d %H:%M}" == "2026-10-01 00:00"
    assert f"{end:%Y-%m-%d %H:%M}" == "2026-10-01 23:59"        # fim inclusivo
    assert job["progress"] == job["total"] == 4
    r = job["result"]
    assert r["metrics"]["chamadas_ia"] == 4 and len(r["trades"]) == 4
    assert r["trades"][0]["side"] == "COMPRA"
    assert r["figure"]["data"][0]["type"] == "candlestick"


def test_validacao_do_formulario():
    client, _ = make_client()
    assert client.post("/api/backtests", json={**BODY, "timeframe": "M7"}).status_code == 422
    assert client.post("/api/backtests", json={**BODY, "start": "2026-10-05"}).status_code == 422
    assert client.post("/api/backtests", json={**BODY, "symbol": ""}).status_code == 422
    assert client.post("/api/backtests", json={**BODY, "max_calls": 0}).status_code == 422
    assert client.get("/api/backtests/nao-existe").status_code == 404


def test_cancelar_backtest():
    started = threading.Event()

    def slow_run(params, on_progress, should_stop):
        class Slow(AlwaysUp):
            def predict(self, prompt):
                started.set()
                time.sleep(0.05)
                return super().predict(prompt)
        return BacktestService(FakeProvider(), Slow(), EchoPrompt()).run(params, on_progress, should_stop)

    client, _ = make_client(slow_run)
    job_id = client.post("/api/backtests", json={**BODY, "max_calls": 100}).json()["id"]
    started.wait(2)
    client.post(f"/api/backtests/{job_id}/cancel")
    job = wait(client, job_id)
    assert job["status"] == "cancelled"
    assert 0 < job["result"]["metrics"]["chamadas_ia"] < 17


def test_erro_aparece_no_job():
    def broken(params, on_progress, should_stop):
        raise RuntimeError("Falha ao conectar no MetaTrader 5")

    client, _ = make_client(broken)
    job = wait(client, client.post("/api/backtests", json=BODY).json()["id"])
    assert job["status"] == "error" and "MetaTrader" in job["error"]
