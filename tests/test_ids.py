from dataclasses import FrozenInstanceError, replace
import pytest
from saferoll.ids import request_id


def test_identity_is_immutable_and_ids_stable(config):
    identity = config.identity
    with pytest.raises(FrozenInstanceError):
        identity.epoch = 2
    assert request_id(identity, 12) == request_id(identity, 12)
    assert request_id(identity, 12) != request_id(identity, 13)
    assert request_id(identity, 12) != request_id(replace(identity, epoch=2), 12)
    with pytest.raises(ValueError):
        request_id(identity, -1)
