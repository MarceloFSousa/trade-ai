import logging

import anthropic

from ..domain import Prediction
from ..interfaces import AIPredictor
from .schema import PREDICTION_SCHEMA, parse_prediction

log = logging.getLogger(__name__)

# Força a resposta estruturada: o Claude é obrigado a "chamar" esta ferramenta.
PREDICTION_TOOL = {
    "name": "submit_prediction",
    "description": "Registra a análise e o sinal para o próximo candle.",
    "input_schema": PREDICTION_SCHEMA,
}


class ClaudePredictor(AIPredictor):
    def __init__(self, model: str, max_tokens: int = 1024, temperature: float | None = None,
                 client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic(max_retries=5)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def predict(self, prompt: str) -> Prediction:
        params = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            tools=[PREDICTION_TOOL],
            tool_choice={"type": "tool", "name": PREDICTION_TOOL["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
        if self._temperature is not None:
            params["temperature"] = self._temperature

        response = self._client.messages.create(**params)
        for block in response.content:
            if block.type == "tool_use" and block.name == PREDICTION_TOOL["name"]:
                return parse_prediction(block.input)

        log.warning("Claude não devolveu a ferramenta de previsão (stop_reason=%s)", response.stop_reason)
        return Prediction.neutral("Resposta sem previsão estruturada.")
