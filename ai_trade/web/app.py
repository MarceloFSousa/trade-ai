import asyncio
import json
from datetime import date, datetime, time
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import plotly.offline
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from ..charts.plotly_renderer import PlotlyRenderer
from ..domain import TIMEFRAMES, Direction
from ..robot.robot import TradingRobot
from ..services.backtest_service import BacktestParams, BacktestRun
from .jobs import Job, JobManager, RunFn

STATIC = Path(__file__).parent / "static"


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str
    start: date
    end: date
    bars: int = Field(ge=1, le=1000)
    step: int = Field(ge=1, le=10000)
    max_calls: int = Field(ge=1, le=10000)
    stop_points: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check(self):
        self.symbol = self.symbol.strip()
        self.timeframe = self.timeframe.upper()
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"Tempo gráfico inválido. Use um de: {', '.join(TIMEFRAMES)}")
        if self.start > self.end:
            raise ValueError("A data de início deve ser anterior ou igual à data de fim.")
        return self


def build_payload(run: BacktestRun, renderer: PlotlyRenderer) -> dict:
    result = run.result
    figure = json.loads(renderer.backtest_figure(run.candles, result).to_json())
    trades = [{
        "time": f"{t.signal_time:%Y-%m-%d %H:%M}",
        "side": "COMPRA" if t.direction is Direction.UP else "VENDA",
        "confidence": t.confidence,
        "entry": t.entry_price,
        "exit": t.exit_price,
        "stopped_out": t.stopped_out,
        "points": t.points,
        "won": t.won,
        "reasoning": t.reasoning,
    } for t in result.trades]
    return {"metrics": result.metrics, "trades": trades, "figure": figure, "total_candles": run.total_candles}


def job_view(job: Job) -> dict:
    p = job.params
    now = monotonic()
    return {
        "id": job.id,
        "status": job.status.value,
        "elapsed": round(now - job.started_at) if job.started_at else 0,
        "waiting": round(now - job.last_update) if job.last_update else 0,   # há quanto tempo espera a IA
        "progress": job.progress,
        "total": job.total,
        "feed": list(job.feed),
        "error": job.error,
        "params": {"symbol": p.symbol, "timeframe": p.timeframe,
                   "start": f"{p.start:%Y-%m-%d}", "end": f"{p.end:%Y-%m-%d}"},
        "result": job.payload,
    }


def same_origin(ws: WebSocket) -> bool:
    """Navegadores não aplicam CORS a WebSocket: sem isto, qualquer site aberto
    na máquina poderia conectar em localhost e comandar o robô."""
    origin = ws.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == ws.headers.get("host")


async def robot_socket(ws: WebSocket, robot: TradingRobot) -> None:
    """Estado a cada segundo para a página; comandos da página para o robô."""
    if not same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()

    async def send_state():
        while True:
            await ws.send_json({"type": "state", **await asyncio.to_thread(robot.snapshot)})
            await asyncio.sleep(1)

    async def receive_commands():
        while True:
            msg = await ws.receive_json()
            try:
                await asyncio.to_thread(robot.command, msg)
                await ws.send_json({"type": "ack", "action": msg.get("action"), "ok": True})
            except (ValueError, TypeError, RuntimeError) as exc:
                await ws.send_json({"type": "ack", "action": msg.get("action"), "ok": False, "error": str(exc)})

    tasks = [asyncio.create_task(send_state()), asyncio.create_task(receive_commands())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if isinstance(task.exception(), Exception) and not isinstance(task.exception(), WebSocketDisconnect):
                raise task.exception()
    finally:
        for task in tasks:
            task.cancel()


def create_app(cfg: dict, run_fn: RunFn, renderer: PlotlyRenderer, robot: TradingRobot | None = None) -> FastAPI:
    """`run_fn` e `robot` vêm do composition root (main.py): a web não conhece MT5 nem a IA."""
    app = FastAPI(title="AI Trade")

    if robot:
        @app.websocket("/ws/robot")
        async def ws_robot(ws: WebSocket):
            await robot_socket(ws, robot)

    jobs = JobManager(run_fn, lambda run: build_payload(run, renderer))
    bt = cfg["backtest"]

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/plotly.min.js", include_in_schema=False)
    def plotly_js():
        # mesma versão do plotly.py que gera as figuras; funciona offline
        return Response(plotly.offline.get_plotlyjs(), media_type="application/javascript",
                        headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/defaults")
    def defaults():
        return {
            "symbol": cfg["symbol"], "timeframe": cfg["timeframe"], "timeframes": TIMEFRAMES,
            "start": str(bt["start"]), "end": str(bt["end"]),
            "bars": cfg["bars"], "step": bt["step"], "max_calls": bt["max_calls"],
            "stop_points": bt.get("stop_points", 0),
            "model": cfg["ai"]["model"],
        }

    @app.post("/api/backtests", status_code=202)
    def start_backtest(req: BacktestRequest):
        params = BacktestParams(
            symbol=req.symbol, timeframe=req.timeframe,
            start=datetime.combine(req.start, time.min),
            end=datetime.combine(req.end, time.max),   # fim inclusivo
            bars=req.bars, step=req.step, max_calls=req.max_calls,
            point_value=bt["point_value"], volume=bt["volume"], stop_points=req.stop_points,
        )
        return job_view(jobs.submit(params))

    @app.get("/api/backtests/{job_id}")
    def get_backtest(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Backtest não encontrado")
        return job_view(job)

    @app.post("/api/backtests/{job_id}/cancel")
    def cancel_backtest(job_id: str):
        job = jobs.cancel(job_id)
        if not job:
            raise HTTPException(404, "Backtest não encontrado")
        return job_view(job)

    return app
