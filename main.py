"""Ponto de entrada (composition root): único lugar que conhece as implementações concretas.

Exemplos:
  python main.py web                      # front-end em http://127.0.0.1:8000
  python main.py analyze --symbol WIN$N --timeframe M5 --bars 50
  python main.py backtest --start 2026-09-01 --end 2026-10-01 --step 5 --max-calls 20
"""
import argparse
import logging
import sys
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

from ai_trade.ai.factory import create_predictor
from ai_trade.charts.plotly_renderer import PlotlyRenderer
from ai_trade.data.mt5_provider import MT5Provider
from ai_trade.execution.mt5_executor import MT5Executor
from ai_trade.execution.paper import PaperExecutor
from ai_trade.prompt.file_prompt import FilePromptBuilder
from ai_trade.robot.robot import RobotParams, RobotSession, TradingRobot
from ai_trade.services.analyzer import Analyzer
from ai_trade.services.backtest_service import BacktestParams, BacktestService

ROOT = Path(__file__).parent


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(ROOT / "config.yaml"))
    common.add_argument("--symbol")
    common.add_argument("--timeframe")
    common.add_argument("--bars", type=int, help="candles enviados à IA")
    common.add_argument("--model", help="modelo da IA")
    common.add_argument("--provider", help="IA registrada em ai_trade/ai/factory.py")
    common.add_argument("--prompt", help="arquivo de prompt")
    common.add_argument("--no-open", action="store_true", help="não abrir o navegador")

    parser = argparse.ArgumentParser(description="Previsão do próximo candle com IA + backtest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("analyze", parents=[common], help="analisa os últimos candles agora")
    bt = sub.add_parser("backtest", parents=[common], help="roda a IA sobre o histórico")
    bt.add_argument("--start", help="AAAA-MM-DD")
    bt.add_argument("--end", help="AAAA-MM-DD")
    bt.add_argument("--step", type=int, help="analisa a cada K candles")
    bt.add_argument("--max-calls", type=int, help="limite de chamadas à IA")
    bt.add_argument("--stop", type=float, help="stop em pontos (0 = sem stop)")
    web = sub.add_parser("web", parents=[common], help="front-end para configurar e rodar o backtest")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def merge(cfg: dict, args: argparse.Namespace) -> dict:
    overrides = {"symbol": args.symbol, "timeframe": args.timeframe, "bars": args.bars, "prompt_file": args.prompt}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if args.model:
        cfg["ai"]["model"] = args.model
    if args.provider:
        cfg["ai"]["provider"] = args.provider
    if args.command == "backtest":
        bt = {"start": args.start, "end": args.end, "step": args.step, "max_calls": args.max_calls,
              "stop_points": args.stop}
        cfg["backtest"].update({k: v for k, v in bt.items() if v is not None})
    return cfg


def output_path(cfg: dict, kind: str, ext: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_symbol = "".join(c if c.isalnum() else "_" for c in cfg["symbol"])
    return ROOT / cfg["output_dir"] / f"{kind}_{safe_symbol}_{cfg['timeframe']}_{stamp}.{ext}"


def show(path: Path, args: argparse.Namespace) -> None:
    print(f"\nGráfico: {path}")
    if not args.no_open:
        webbrowser.open(path.resolve().as_uri())


def save_trades_csv(cfg: dict, trades) -> Path:
    csv = output_path(cfg, "trades", "csv")
    csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{**asdict(t), "direction": t.direction.value} for t in trades]).to_csv(csv, index=False)
    return csv


def run_analyze(cfg, args) -> None:
    with MT5Provider() as provider:
        analyzer = Analyzer(provider, create_predictor(cfg["ai"]), FilePromptBuilder(ROOT / cfg["prompt_file"]))
        analysis = analyzer.analyze(cfg["symbol"], cfg["timeframe"], cfg["bars"])
    p = analysis.prediction
    print(f"{cfg['symbol']} {cfg['timeframe']} | último candle: {analysis.candles['time'].iloc[-1]}")
    print(f"Previsão: {p.direction.value} (confiança {p.confidence:.0%})")
    print(f"Raciocínio: {p.reasoning}")
    show(PlotlyRenderer().render_analysis(cfg["symbol"], cfg["timeframe"], analysis.candles, p,
                                          output_path(cfg, "analise", "html")), args)


def run_backtest(cfg, args) -> None:
    bt = cfg["backtest"]
    params = BacktestParams(
        symbol=cfg["symbol"], timeframe=cfg["timeframe"],
        start=datetime.fromisoformat(str(bt["start"])), end=datetime.fromisoformat(str(bt["end"])),
        bars=cfg["bars"], step=bt["step"], max_calls=bt["max_calls"],
        point_value=bt["point_value"], volume=bt["volume"], stop_points=bt.get("stop_points", 0),
    )

    def progress(n, total, time, pred):
        print(f"[{n}/{total}] {time:%Y-%m-%d %H:%M} -> {pred.direction.value} ({pred.confidence:.0%})")

    with MT5Provider() as provider:
        service = BacktestService(provider, create_predictor(cfg["ai"]), FilePromptBuilder(ROOT / cfg["prompt_file"]))
        run = service.run(params, on_progress=progress)
    result = run.result

    if result.stopped:
        print(f"\nInterrompido: {result.stop_reason}")
    print("\n=== Resultado ===")
    for k, v in result.metrics.items():
        print(f"{k:>20}: {v}")
    if result.trades:
        print(f"\nTrades: {save_trades_csv(cfg, result.trades)}")
    show(PlotlyRenderer().render_backtest(run.candles, result, output_path(cfg, "backtest", "html")), args)


def run_web(cfg, args) -> None:
    import uvicorn
    from ai_trade.web.app import create_app

    def run_job(params: BacktestParams, on_progress, should_stop):
        # cada job abre a própria conexão e cria a IA; erros (ex.: sem API key) aparecem no front-end
        load_dotenv(ROOT / ".env", override=True)   # relê o .env: editar a chave não exige reiniciar
        with MT5Provider() as provider:
            service = BacktestService(provider, create_predictor(cfg["ai"]),
                                      FilePromptBuilder(ROOT / cfg["prompt_file"]))
            run = service.run(params, on_progress, should_stop)
        if run.result.trades:
            save_trades_csv({**cfg, "symbol": params.symbol, "timeframe": params.timeframe}, run.result.trades)
        return run

    def open_robot_session(mode: str) -> RobotSession:
        load_dotenv(ROOT / ".env", override=True)
        provider = MT5Provider()
        provider.connect()
        try:
            executor = PaperExecutor(provider) if mode == "paper" else MT5Executor()
            return RobotSession(provider, provider, executor, create_predictor(cfg["ai"]),
                                FilePromptBuilder(ROOT / cfg["prompt_file"]), provider.close)
        except Exception:
            provider.close()
            raise

    robot_cfg = dict(cfg["robot"])
    allow_real = robot_cfg.pop("allow_real_account", False)
    robot = TradingRobot(RobotParams(**robot_cfg), open_robot_session, allow_real)

    app = create_app(cfg, run_job, PlotlyRenderer(), robot)
    url = f"http://{args.host}:{args.port}"
    print(f"Front-end em {url}  (Ctrl+C para parar)")
    if not args.no_open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv(ROOT / ".env")
    args = parse_args()
    cfg = merge(load_config(Path(args.config)), args)
    {"analyze": run_analyze, "backtest": run_backtest, "web": run_web}[args.command](cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
