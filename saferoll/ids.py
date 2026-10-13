"""Stable scientific identities independent of filesystem locations."""
from __future__ import annotations
from dataclasses import dataclass
import json
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class RolloutIdentity:
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str


def request_id(identity: RolloutIdentity, request_index: int) -> str:
    """Use an unambiguous UUID5 name; indices are zero-based."""
    if request_index < 0:
        raise ValueError("request_index must be nonnegative")
    name = json.dumps([identity.rollout_id, identity.epoch, "request", request_index],
                      separators=(",", ":"), ensure_ascii=False)
    return str(uuid5(NAMESPACE_URL, name))
