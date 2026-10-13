from dataclasses import FrozenInstanceError
import pytest
from saferoll.lifecycle import (ALLOWED_TRANSITIONS, DECISION_TARGETS, Decision, Outcome,
                               RolloutCoordinator, RolloutState as S, validate_edge)


def coordinator(config, max_holds=1):
    return RolloutCoordinator(config.identity, 0.2, max_holds)


def evaluate(c):
    for index, state in enumerate([S.CANDIDATE_CREATED, S.VALIDATING, S.CANARY, S.EVALUATING], 1):
        c.transition(state, index, 'test setup')


@pytest.mark.parametrize('source', list(S))
@pytest.mark.parametrize('target', list(S))
def test_entire_transition_graph(source, target):
    decision = next((d for d, s in DECISION_TARGETS.items() if s == target), None)
    if target in ALLOWED_TRANSITIONS[source]:
        validate_edge(source, target, decision if source == S.EVALUATING else None)
    else:
        with pytest.raises(ValueError):
            validate_edge(source, target, decision)


def test_enums_and_decisions_require_evaluation(config):
    assert {d.value for d in Decision} == {'PROMOTE', 'HOLD', 'ROLLBACK'}
    c = coordinator(config)
    for decision in Decision:
        with pytest.raises(ValueError):
            c.apply_decision(decision, 1, 'invalid')
    c.transition(S.CANDIDATE_CREATED, 1, 'candidate')
    c.transition(S.VALIDATING, 2, 'validation')
    before = c.snapshot
    with pytest.raises(ValueError):
        c.transition(S.COMPLETED, 3, 'invalid')
    assert c.snapshot == before
    with pytest.raises(FrozenInstanceError):
        c.snapshot.desired_state = S.COMPLETED


@pytest.mark.parametrize('decision', list(Decision))
def test_decision_must_match_target(decision):
    for target in DECISION_TARGETS.values():
        if target != DECISION_TARGETS[decision]:
            with pytest.raises(ValueError):
                validate_edge(S.EVALUATING, target, decision)
    with pytest.raises(ValueError):
        validate_edge(S.EVALUATING, DECISION_TARGETS[decision])
    with pytest.raises(ValueError):
        validate_edge(S.HOLDING, S.EVALUATING, decision)


def test_desired_observed_separation_and_pending_convergence(config):
    c = coordinator(config)
    c.transition(S.CANDIDATE_CREATED, 1, 'create')
    c.transition(S.VALIDATING, 2, 'validate')
    c.transition(S.CANARY, 3, 'desired canary', converge=False)
    assert c.snapshot.desired_state == S.CANARY
    assert c.snapshot.observed_state == S.VALIDATING
    assert c.snapshot.desired_candidate_weight == .2
    assert c.snapshot.observed_candidate_weight == 0
    with pytest.raises(ValueError):
        c.transition(S.EVALUATING, 4, 'too soon')
    with pytest.raises(ValueError):
        c.observe_state(S.COMPLETED, 4, 'wrong observation')
    c.observe_state(S.CANARY, 4, 'observed convergence')
    assert c.snapshot.desired_state == c.snapshot.observed_state == S.CANARY
    assert c.snapshot.observed_candidate_weight == .2
    assert c.history[-1].kind == 'OBSERVATION'
    assert len(c.history) == 5


@pytest.mark.parametrize('decision,weight', [(Decision.PROMOTE, 1), (Decision.ROLLBACK, 0), (Decision.HOLD, .2)])
def test_delayed_decision_observation(config, decision, weight):
    c = coordinator(config)
    evaluate(c)
    c.apply_decision(decision, 5, 'decision', converge=False)
    assert c.snapshot.observed_state == S.EVALUATING
    assert c.snapshot.observed_candidate_weight == .2
    assert c.snapshot.desired_candidate_weight == weight
    c.observe_state(DECISION_TARGETS[decision], 6, 'converged')
    assert c.snapshot.observed_candidate_weight == weight
    if decision == Decision.PROMOTE:
        c.transition(S.COMPLETED, 7, 'completed', converge=False)
        assert c.final_outcome is None
        c.observe_state(S.COMPLETED, 8, 'completion observed')
        assert c.final_outcome == Outcome.PROMOTED


def test_hold_bound_and_terminal_state(config):
    c = coordinator(config, max_holds=1)
    evaluate(c)
    c.apply_decision(Decision.HOLD, 5, 'hold')
    assert c.snapshot.observed_state == S.HOLDING
    c.transition(S.EVALUATING, 6, 'released')
    c.apply_decision(Decision.HOLD, 7, 'hold')
    assert c.snapshot.observed_state == S.FAILED
    assert c.final_outcome == Outcome.FAILED
    assert c.hold_count == 1 and c.decision_count == 2
    assert c.history[-1].reason == 'hold bound exceeded'
    with pytest.raises(ValueError):
        c.transition(S.COMPLETED, 8, 'invalid')


def test_time_and_reason_validation_atomic(config):
    c = coordinator(config)
    for time, reason in [(0, 'backwards'), (True, 'boolean'), (1, '')]:
        with pytest.raises(ValueError):
            c.transition(S.CANDIDATE_CREATED, time, reason)
        assert len(c.history) == 1
    evaluate(c)
    with pytest.raises(ValueError):
        c.transition(S.PROMOTING, 5, 'missing decision')
    with pytest.raises(ValueError):
        c.transition(S.PROMOTING, 5, 'bypass', decision=Decision.PROMOTE)
    with pytest.raises(ValueError):
        c.apply_decision(Decision.HOLD, 4, 'invalid clock')
    assert c.decision_count == c.hold_count == 0
