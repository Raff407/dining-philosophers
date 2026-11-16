import itertools
import random

import pytest

from sharded_dining import backfill_choice, period_bound, schedule, simulate


def independent(n, seats):
    return all((s + 1) % n not in seats for s in seats)


@pytest.mark.parametrize("n", range(3, 41))
def test_schedule_is_maximum_independent_and_fair(n):
    for t in range(2 * n):
        s = schedule(n, t)
        assert len(s) == n // 2 and independent(n, s)
    p = period_bound(n)
    for seat in range(n):
        for t0 in range(2 * n):            # every window of p shifts
            assert any(seat in schedule(n, t) for t in range(t0, t0 + p))


def max_independent_size(n, cand):
    for k in range(len(cand), -1, -1):
        if any(independent(n, set(c)) for c in itertools.combinations(cand, k)):
            return k


@pytest.mark.parametrize("n", range(3, 11))
def test_backfill_is_optimal(n):
    rng = random.Random(n)
    for _ in range(200):
        cand = {s for s in range(n) if rng.random() < 0.6}
        if len(cand) == n:
            cand.discard(rng.randrange(n))
        arrival = [(rng.random(), s) for s in range(n)]
        chosen = backfill_choice(n, cand, arrival)
        assert set(chosen) <= cand and independent(n, set(chosen))
        assert len(chosen) == max_independent_size(n, sorted(cand))


@pytest.mark.parametrize("n,arc", [(3, 1), (4, 2), (5, 2), (6, 3), (7, 3), (9, 4), (12, 5)])
def test_simulation_is_safe_and_starvation_free(n, arc):
    r = simulate(n=n, arc_size=arc, meals=4, seed=n)
    assert r["meals"] == [4] * n
    assert max(r["max_wait_shifts"]) <= r["wait_bound"]
    assert r["max_concurrent"] <= r["ceiling"]
