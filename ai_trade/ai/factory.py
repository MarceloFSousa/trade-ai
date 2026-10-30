"""Registro de IAs. Para adicionar uma nova (OpenAI, Gemini...), implemente
AIPredictor e registre um construtor aqui; o resto do sistema não muda."""
import os
from typing import Callable

from ..interfaces import AIPredictor


def _claude(cfg: dict) -> AIPredictor:
    from .claude_predictor import ClaudePredictor
    return ClaudePredictor(
        model=cfg["model"],
        max_tokens=cfg.get("max_tokens", 1024),
        temperature=cfg.get("temperature"),
    )


def _gemini(cfg: dict) -> AIPredictor:
    from .gemini_predictor import GeminiPredictor
    return GeminiPredictor(
        model=cfg["model"],
        max_tokens=cfg.get("max_tokens", 1024),
        temperature=cfg.get("temperature"),
        min_interval=cfg.get("min_interval_seconds", 0.0),
        timeout=cfg.get("timeout_seconds", 120.0),
        api_key=os.environ.get("GEMINI_API_KEY") or None,
    )


PREDICTORS: dict[str, Callable[[dict], AIPredictor]] = {
    "claude": _claude,
    "gemini": _gemini,
}


def create_predictor(cfg: dict) -> AIPredictor:
    name = cfg.get("provider", "claude")
    try:
        return PREDICTORS[name](cfg)
    except KeyError:
        raise ValueError(f"IA '{name}' não registrada. Disponíveis: {', '.join(PREDICTORS)}")
