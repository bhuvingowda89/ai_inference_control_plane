"""Seeded local RNG routing with one draw per request in index order."""
from __future__ import annotations
import random
from .config import RoutingConfig


class WeightedRouter:
    def __init__(self, config: RoutingConfig, seed: int) -> None:
        self._rng = random.Random(seed)
        self._candidate_weight = config.candidate_weight

    def route(self) -> str:
        return "candidate" if self._rng.random() < self._candidate_weight else "baseline"
