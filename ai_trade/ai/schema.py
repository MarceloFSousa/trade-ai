"""Contrato de resposta da IA, igual para todas as implementações de AIPredictor."""
import logging

from ..domain import Direction, Prediction

log = logging.getLogger(__name__)

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "structure": {"type": "string", "enum": ["uptrend", "downtrend", "range"]},
        "key_levels": {
            "type": "object",
            "properties": {
                "support": {"type": "array", "items": {"type": "number"}},
                "resistance": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["support", "resistance"],
        },
        "bullish_factors": {"type": "array", "items": {"type": "string"}},
        "bearish_factors": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
        "reasoning": {"type": "string"},
        "signal": {"type": "string", "enum": [d.value for d in Direction]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["structure", "key_levels", "bullish_factors", "bearish_factors",
                 "invalidation", "reasoning", "signal", "confidence"],
}


def parse_prediction(data: dict) -> Prediction:
    """Converte a resposta em Prediction; resposta inválida vira NEUTRAL (não opera)."""
    try:
        direction = Direction(str(data["signal"]).upper())
        confidence = min(max(float(data["confidence"]), 0.0), 1.0)
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("Resposta inválida da IA: %s (%s)", data, exc)
        return Prediction.neutral(f"Resposta inválida: {data}")
    return Prediction(direction, confidence, _describe(data))


def _describe(data: dict) -> str:
    levels = data.get("key_levels") or {}
    parts = [str(data.get("reasoning", "")).strip()]
    if data.get("structure"):
        parts.append(f"Estrutura: {data['structure']}.")
    if levels.get("support") or levels.get("resistance"):
        parts.append(f"Suportes: {levels.get('support') or '-'}. Resistências: {levels.get('resistance') or '-'}.")
    if data.get("bullish_factors"):
        parts.append("Alta: " + "; ".join(map(str, data["bullish_factors"])) + ".")
    if data.get("bearish_factors"):
        parts.append("Baixa: " + "; ".join(map(str, data["bearish_factors"])) + ".")
    if data.get("invalidation"):
        parts.append(f"Invalidação: {data['invalidation']}.")
    return " ".join(p for p in parts if p)
