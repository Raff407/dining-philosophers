"""
Dining philosophers: sharded arbitration with rotational scheduling,
opportunistic backfill and bounded time slices.

Philosopher p_i needs chopsticks c_{i-1} and c_i. Seats are split into
contiguous arcs; each arc has an arbitrator (a passive monitor: one lock).
A shared clock advances the shift every `slice_s` seconds, preempting
every diner, then grants the scheduled set S_t and backfills idle capacity.
"""
from __future__ import annotations

import random
import threading
import time
from collections import Counter
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager
from enum import Enum


class State(Enum):
    THINKING = "thinking"
    HUNGRY = "hungry"
    EATING = "eating"


class InvariantViolation(RuntimeError):
    


def check(condition: bool, message: str) -> None:
    # Explicit check rather than `assert`, so it survives `python -O`.
    if not condition:
        raise InvariantViolation(message)


# ---------------------------------------------------------------------------
# Pure scheduling logic (no threads) - this is what the proofs are about.
# ---------------------------------------------------------------------------

def schedule(n: int, t: int) -> frozenset[int]:
    """Scheduled set S_t = {(t + 2k) mod n : 0 <= k < floor(n/2)}."""
    return frozenset((t + 2 * k) % n for k in range(n // 2))


def period_bound(n: int) -> int:
    return 2 if n % 2 == 0 else 3


def backfill_choice(n: int, candidates: set[int], arrival: list) -> list[int]:
    if not candidates:
        return []
    check(len(candidates) < n, "candidates cannot cover the whole cycle")

    # Start just after a non-candidate seat so that no run wraps around.
    start = next(seat for seat in range(n) if seat not in candidates)
    runs: list[list[int]] = []
    run: list[int] = []
    for step in range(1, n + 1):
        seat = (start + step) % n
        if seat in candidates:
            run.append(seat)
        elif run:
            runs.append(run)
            run = []

    chosen: list[int] = []
    for run in runs:
        offset = 0
        if len(run) % 2 == 0:
            earliest = min(range(len(run)), key=lambda k: arrival[run[k]])
            offset = earliest % 2
        chosen.extend(run[offset::2])
    return chosen


# ---------------------------------------------------------------------------
# Concurrent table
# ---------------------------------------------------------------------------

class Arbitrator:
    def __init__(self, index: int, seats: range) -> None:
        self.index = index
        self.seats = seats
        self.lock = threading.Lock()


class Table:
    def __init__(self, n: int, arc_size: int, slice_s: float) -> None:
        if n < 3:
            raise ValueError("need at least 3 philosophers")
        if arc_size < 1:
            raise ValueError("arc_size must be positive")
        self.n = n
        self.slice_s = slice_s
        self.arcs = [Arbitrator(a, range(s, min(s + arc_size, n)))
                     for a, s in enumerate(range(0, n, arc_size))]
        self.arc_of = [seat // arc_size for seat in range(n)]

        self.state = [State.THINKING] * n
        self.holder: list[int | None] = [None] * n
        self.arrival: list[tuple[int, int]] = [(0, i) for i in range(n)]
        self.hungry_since = [0] * n  
        self.granted = [threading.Event() for _ in range(n)]
        self.preempt = [threading.Event() for _ in range(n)]

        self.shift = 0
        self.running = True  

        self._pending: set[int] = set() 
        self._pending_lock = threading.Lock()
        self._drained = threading.Event()

        # Instrumentation only.
        self.meals = [0] * n
        self.preemptions = [0] * n
        self.max_wait = [0] * n
        self._stats_lock = threading.Lock()
        self.grant_kinds: Counter[str] = Counter()
        self.eating_now = 0
        self.max_concurrent = 0

    def _nbrs(self, i: int) -> tuple[int, int]:
        return (i - 1) % self.n, (i + 1) % self.n

    @contextmanager
    def _locked(self, seats: Iterable[int]):
        arcs = sorted({self.arc_of[s % self.n] for s in seats})
        with ExitStack() as stack:
            for a in arcs:    # asc order
                stack.enter_context(self.arcs[a].lock)
            yield

    def _can_eat(self, i: int) -> bool:
        return (self.running
                and self.state[i] is State.HUNGRY
                and all(self.state[j] is not State.EATING
                        for j in self._nbrs(i)))

    def _grant(self, i: int, kind: str) -> None:
        left, right = (i - 1) % self.n, i
        check(self.holder[left] is None and self.holder[right] is None,
              f"chopstick double-allocated at seat {i}")
        self.holder[left] = self.holder[right] = i
        self.state[i] = State.EATING

        wait = self.shift - self.hungry_since[i]
        check(wait <= period_bound(self.n),
              f"seat {i} waited {wait} shifts (bound {period_bound(self.n)})")
        self.max_wait[i] = max(self.max_wait[i], wait)
        with self._stats_lock:
            self.grant_kinds[kind] += 1
            self.eating_now += 1
            self.max_concurrent = max(self.max_concurrent, self.eating_now)
            check(self.eating_now <= self.n // 2, "more than floor(N/2) eating")

        self.preempt[i].clear()
        self.granted[i].set()

    def request(self, i: int) -> None:
        """Become hungry and block until granted both chopsticks."""
        with self._locked(range(i - 1, i + 2)):
            self.state[i] = State.HUNGRY
            self.arrival[i] = (time.monotonic_ns(), i)
            self.hungry_since[i] = self.shift
            if self._can_eat(i):
                self._grant(i, "opportunistic")
        self.granted[i].wait()

    def await_grant(self, i: int) -> None:
        """Block until re-granted after a preemption."""
        self.granted[i].wait()

    def release(self, i: int, finished: bool) -> None:
        """Put both chopsticks down; if not finished, stay hungry."""
        with self._locked(range(i - 2, i + 3)):
            check(self.state[i] is State.EATING, f"seat {i} released while not eating")
            self.holder[(i - 1) % self.n] = self.holder[i] = None
            self.granted[i].clear()
            with self._stats_lock:
                self.eating_now -= 1
            if finished:
                self.state[i] = State.THINKING
                self.meals[i] += 1
            else:
                self.state[i] = State.HUNGRY      
                self.hungry_since[i] = self.shift
                self.preemptions[i] += 1
            if self.running:
                for j in sorted(self._nbrs(i), key=lambda s: self.arrival[s]):
                    if self._can_eat(j):
                        self._grant(j, "opportunistic")
        with self._pending_lock:
            if i in self._pending:
                self._pending.discard(i)
                if not self._pending:
                    self._drained.set()

    def advance_shift(self) -> None:
        """Preempt all diners, advance t, grant S_t, then backfill."""
        everyone = range(self.n)
        with self._locked(everyone):
            self.running = False
            eaters = [i for i in everyone if self.state[i] is State.EATING]
            with self._pending_lock:
                self._pending = set(eaters)
                if eaters:
                    self._drained.clear()
                else:
                    self._drained.set()
            for i in eaters:
                self.preempt[i].set()

        self._drained.wait()     # every diner has put its chopsticks down

        with self._locked(everyone):
            self.shift += 1
            self.running = True
            scheduled = schedule(self.n, self.shift)
            for i in sorted(scheduled):  
                if self.state[i] is State.HUNGRY:
                    check(self._can_eat(i), "scheduled set not independent")
                    self._grant(i, "scheduled")
            candidates = {i for i in everyone
                          if i not in scheduled and self._can_eat(i)}
            for i in backfill_choice(self.n, candidates, self.arrival):
                check(self._can_eat(i), "backfill broke independence")
                self._grant(i, "backfill")

def run_clock(table: Table, stop: threading.Event) -> None:
    while not stop.wait(table.slice_s):
        table.advance_shift()


def philosopher(table: Table, i: int, meals: int, rng: random.Random,
                think: tuple[float, float], meal: tuple[float, float]) -> None:
    for _ in range(meals):
        # think
        time.sleep(rng.uniform(*think))
        remaining = rng.uniform(*meal)
        # request
        table.request(i)
        # eat, maybe in slices
        while True:    
            started = time.monotonic()
            preempted = table.preempt[i].wait(timeout=remaining)
            remaining -= time.monotonic() - started
            finished = not preempted or remaining <= 0
            # release
            table.release(i, finished)
            if finished:
                break
            table.await_grant(i)


def simulate(n: int = 7, arc_size: int = 3, meals: int = 5,
             slice_s: float = 0.02, think: tuple[float, float] = (0.0, 0.03),
             meal: tuple[float, float] = (0.005, 0.06), seed: int = 0,
             timeout_s: float = 60.0) -> dict:
    table = Table(n, arc_size, slice_s)
    errors: list[Exception] = []

    def guarded(fn, *args):
        try:
            fn(*args)
        except Exception as exc: 
            errors.append(exc)

    stop = threading.Event()
    clock = threading.Thread(target=guarded, args=(run_clock, table, stop),
                             daemon=True)
    diners = [threading.Thread(target=guarded,
                               args=(philosopher, table, i, meals,
                                     random.Random(seed * 1000 + i), think, meal),
                               daemon=True)
              for i in range(n)]
    clock.start()
    for d in diners:
        d.start()
    deadline = time.monotonic() + timeout_s
    for d in diners:
        d.join(timeout=max(0.0, deadline - time.monotonic()))
    stop.set()
    clock.join(timeout=5)

    if errors:
        raise errors[0]
    if any(d.is_alive() for d in diners):
        raise RuntimeError("philosophers did not finish (deadlock or livelock)")
    return {
        "n": n,
        "shifts": table.shift,
        "meals": table.meals,
        "preemptions": table.preemptions,
        "max_wait_shifts": table.max_wait,
        "wait_bound": period_bound(n),
        "grants": dict(table.grant_kinds),
        "max_concurrent": table.max_concurrent,
        "ceiling": n // 2,
    }


if __name__ == "__main__":
    report = simulate(n=7, arc_size=3, meals=6, seed=42)
    for key, value in report.items():
        print(f"{key:>16}: {value}")
