import random
from saferoll.config import RoutingConfig
from saferoll.router import WeightedRouter


def test_seeded_routing_and_no_global_rng(config):
    a = WeightedRouter(config.routing, 101)
    b = WeightedRouter(config.routing, 101)
    state = random.getstate()
    assignments = [a.route() for _ in range(10000)]
    assert assignments == [b.route() for _ in range(10000)]
    assert random.getstate() == state
    assert 0.18 < assignments.count("candidate") / 10000 < 0.22
    c = WeightedRouter(config.routing, 102)
    assert assignments != [c.route() for _ in range(10000)]


def test_extreme_weights():
    assert {WeightedRouter(RoutingConfig(1, 0), 1).route() for _ in range(20)} == {"baseline"}
    assert {WeightedRouter(RoutingConfig(0, 1), 1).route() for _ in range(20)} == {"candidate"}
