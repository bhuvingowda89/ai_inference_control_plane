"""Seeded local RNG routing with one draw per request in index order."""
from __future__ import annotations
import random
from .config import RoutingConfig


class WeightedRouter:
    def __init__(self, config: RoutingConfig, seed: int) -> None:
        self._rng = random.Random(seed)
        self._candidate_weight = config.candidate_weight

    def observe_candidate_weight(self, weight: float) -> None:
        """Apply observed routing without resetting the seeded request stream."""
        if not 0 <= weight <= 1:
            raise ValueError("candidate weight must be in [0,1]")
        self._candidate_weight = weight

    def route(self) -> str:
        return "candidate" if self._rng.random() < self._candidate_weight else "baseline"
