import json
import logging
import threading
import time

from google import genai
from google.genai import errors, types

from ..domain import AIQuotaError, Prediction
from ..interfaces import AIPredictor
from .schema import PREDICTION_SCHEMA, parse_prediction

log = logging.getLogger(__name__)


class GeminiPredictor(AIPredictor):
    """Google Gemini. O plano gratuito limita chamadas por minuto e por dia:
    `min_interval` espaça as chamadas e o SDK repete em caso de 429/503."""

    def __init__(self, model: str, max_tokens: int = 1024, temperature: float | None = None,
                 min_interval: float = 0.0, timeout: float = 120.0, api_key: str | None = None,
                 client: genai.Client | None = None):
        retry = types.HttpRetryOptions(attempts=5, initial_delay=5, max_delay=60,
                                       http_status_codes=[429, 500, 503])
        # sem timeout uma resposta lenta do plano gratuito prende o backtest indefinidamente
        http = types.HttpOptions(timeout=int(timeout * 1000), retry_options=retry)
        self._client = client or genai.Client(api_key=api_key, http_options=http)
        self._model = model
        self._config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=PREDICTION_SCHEMA,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        self._min_interval = min_interval
        self._last_call = 0.0
        self._lock = threading.Lock()

    def predict(self, prompt: str) -> Prediction:
        self._throttle()
        try:
            response = self._client.models.generate_content(model=self._model, contents=prompt, config=self._config)
        except errors.ClientError as exc:
            if exc.code == 429:
                raise AIQuotaError(f"Limite do plano gratuito do Gemini atingido: {exc.message}") from exc
            raise
        return self._parse(response.text)

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_call + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    @staticmethod
    def _parse(text: str | None) -> Prediction:
        try:
            return parse_prediction(json.loads(text or ""))
        except json.JSONDecodeError:
            log.warning("Resposta do Gemini não é JSON: %r", text)
            return Prediction.neutral(f"Resposta inválida: {text}")
