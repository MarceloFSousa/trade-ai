"""Execução de backtests em segundo plano, um por vez.

Um backtest faz muitas chamadas à IA e pode levar minutos, então a API só
enfileira o job e o front-end consulta o progresso. Um único worker evita
acessos concorrentes ao MetaTrader 5, que não é thread-safe.
"""
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..services.backtest_service import BacktestParams, BacktestRun

log = logging.getLogger(__name__)

RunFn = Callable[[BacktestParams, Callable, Callable[[], bool]], BacktestRun]
PayloadFn = Callable[[BacktestRun], dict]


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class Job:
    id: str
    params: BacktestParams
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    total: int = 0
    feed: list[dict] = field(default_factory=list)   # últimas previsões, para acompanhar ao vivo
    error: str | None = None
    payload: dict | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started_at: float | None = None     # time.monotonic()
    last_update: float | None = None    # última resposta da IA (ou início)


class JobManager:
    FEED_SIZE = 30

    def __init__(self, run_fn: RunFn, to_payload: PayloadFn):
        self._run_fn = run_fn
        self._to_payload = to_payload
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")

    def submit(self, params: BacktestParams) -> Job:
        job = Job(uuid.uuid4().hex[:12], params)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job:
            job.cancel_event.set()
        return job

    def _run(self, job: Job) -> None:
        if job.cancel_event.is_set():
            job.status = JobStatus.CANCELLED
            return
        job.status = JobStatus.RUNNING
        job.started_at = job.last_update = time.monotonic()

        def on_progress(n, total, candle_time, prediction):
            job.progress, job.total = n, total
            job.last_update = time.monotonic()
            job.feed.append({"n": n, "time": f"{candle_time:%Y-%m-%d %H:%M}", "direction": prediction.direction.value,
                             "confidence": prediction.confidence})
            del job.feed[:-self.FEED_SIZE]

        try:
            run = self._run_fn(job.params, on_progress, job.cancel_event.is_set)
            job.payload = self._to_payload(run)
            job.status = JobStatus.CANCELLED if run.result.stopped else JobStatus.DONE
            if run.result.stopped and not job.cancel_event.is_set():
                job.error = run.result.stop_reason   # ex.: cota da IA esgotada
        except Exception as exc:
            log.exception("Backtest %s falhou", job.id)
            job.error = str(exc) or exc.__class__.__name__
            job.status = JobStatus.ERROR
