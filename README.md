		# My Solution of the Philosophers Dining Problem through Shared Arbitration with Rotational Scheduling, Opportunistic Backfill and Bounded Time Slices

*A deadlock-free, starvation-free solution to the dining philosophers problem with a constant, real-time bound on waiting.*

---

## Abstract

The dining philosophers problem asks how $N$ processes arranged in a ring, each needing both of the resources it shares with its two neighbours, can be coordinated so that no resource is ever used by two processes at once, the system never deadlocks, and no process starves. This write-up presents a design that combines four mechanisms:

1. **Sharded arbitration.** The table is split into arcs, each guarded by its own monitor, so decisions in different parts of the table proceed in parallel.
2. **Rotational scheduling.** A fixed, rotating set of non-adjacent philosophers is guaranteed the right to eat in each *shift*. This is the fairness floor.
3. **Opportunistic backfill.** Capacity left idle by scheduled philosophers who are not hungry is handed to unscheduled philosophers, using an allocation that is provably optimal.
4. **Bounded time slices.** Every meal is preempted at the end of the shift, which bounds the length of a shift and turns the fairness guarantee into a bound in *real time*.

The design guarantees mutual exclusion, freedom from deadlock and starvation freedom. Every hungry philosopher is granted its chopsticks within **2 shifts for even $N$** and **3 shifts for odd $N$**, independent of $N$. A tested Python implementation is included.

---

## Quick start

Requires Python 3.10+. The implementation uses only the standard library.

```bash
python sharded_dining.py          # run a 7-philosopher simulation
pip install -r requirements-dev.txt
python -m pytest                  # 53 tests
```

---

## 1. Problem statement

$N \ge 3$ philosophers sit around a circular table with one chopstick between each adjacent pair. A philosopher alternates between *thinking* and *eating*, and needs both adjacent chopsticks to eat. A solution must provide:

| Property | Meaning |
|---|---|
| **Mutual exclusion** | No chopstick is held by two philosophers at the same time. |
| **Deadlock freedom** | The system never reaches a state where no philosopher can ever proceed. |
| **Starvation freedom** | Every philosopher that becomes hungry eventually eats. |
| **Concurrency** | Non-conflicting philosophers should be able to eat at the same time. |

The naive solution, where each philosopher picks up the left chopstick and then the right, deadlocks when all $N$ pick up their left chopstick simultaneously. That state satisfies all four Coffman conditions: mutual exclusion, hold and wait, no preemption and circular wait.

---

## 2. Model

### 2.1 Notation

Index philosophers by $\mathbb{Z}_N = \{0, 1, \dots, N-1\}$, with arithmetic mod $N$. Chopstick $c_i$ lies between $p_i$ and $p_{i+1}$, so philosopher $p_i$ requires

$$R(p_i) = \{c_{i-1},\ c_i\}.$$

### 2.2 Conflict is adjacency

Two philosophers conflict iff they share a chopstick:

$$R(p_i) \cap R(p_j) \neq \varnothing \iff j \equiv i \pm 1 \pmod N.$$

So the conflict structure is exactly the **cycle graph** $C_N$, with philosophers as vertices and chopsticks as edges. A set of philosophers $E$ can eat simultaneously iff $E$ is an **independent set** of $C_N$ (no two members adjacent). The maximum number that can eat at once is the independence number

$$\alpha(C_N) = \left\lfloor \frac{N}{2} \right\rfloor.$$

**Locality.** Whether $p_i$ may eat depends only on $p_{i-1}$ and $p_{i+1}$. Nothing at distance two or more can affect the decision. Every architectural choice below depends on this fact.

### 2.3 Philosopher states

Each philosopher is a thread cycling through

$$\textsf{THINKING} \rightarrow \textsf{HUNGRY} \rightarrow \textsf{EATING} \rightarrow \textsf{THINKING}.$$

A preempted philosopher moves from $\textsf{EATING}$ back to $\textsf{HUNGRY}$ and keeps its remaining meal time.

---

## 3. Design overview

```
           shared shift clock  (advances t every Δ seconds)
                     │
     ┌───────────────┼────────────────┐
     ▼               ▼                ▼
┌──────────┐   ┌──────────┐     ┌──────────┐
│ Arbiter 0│   │ Arbiter 1│ ... │ Arbiter k│   one lock per arc
│ p0 p1 p2 │◄─►│ p3 p4 p5 │     │ ...  pN-1│   seams: ascending lock order
└──────────┘   └──────────┘     └──────────┘
```

Time is divided into **shifts** of length $\Delta$, the *time slice*. At each **shift boundary**:

1. **Preempt.** Every philosopher currently eating is told to put its chopsticks down. Partial progress is kept.
2. **Drain.** Wait until every diner has released.
3. **Advance.** $t \leftarrow t + 1$.
4. **Poll.** Every *scheduled* philosopher ($p_i \in S_t$) that is hungry is granted its chopsticks.
5. **Backfill.** Idle capacity is offered to hungry *unscheduled* philosophers.

During a shift, whenever chopsticks are freed (a meal finishes) or a philosopher becomes hungry, the arbitrators make **opportunistic** grants to any hungry philosopher with no eating neighbour.

---

## 4. Rotational schedule

### 4.1 Definition

Let $m = \lfloor N/2 \rfloor$. The scheduled set at shift $t$ is

$$S_t = \{\, (t + 2k) \bmod N \;:\; 0 \le k < m \,\}.$$

For even $N$ this is just the parity class of $t$. For odd $N$ it is every other seat starting from $t$, which leaves a single *gap of three* (two adjacent unscheduled seats). The schedule for $N = 5$ ($\bullet$ = scheduled) is:

| Shift $t$ | $p_0$ | $p_1$ | $p_2$ | $p_3$ | $p_4$ |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 | $\bullet$ |  | $\bullet$ |  |  |
| 1 |  | $\bullet$ |  | $\bullet$ |  |
| 2 |  |  | $\bullet$ |  | $\bullet$ |
| 3 | $\bullet$ |  |  | $\bullet$ |  |
| 4 |  | $\bullet$ |  |  | $\bullet$ |

### 4.2 Lemma 1: every $S_t$ is a maximum independent set

*Proof.* $S_0 = \{0, 2, \dots, 2(m-1)\}$. Consecutive members differ by $2$, and the wrap-around gap from $2(m-1)$ back to $0$ is $N - 2(m-1)$, which is $2$ for even $N$ and $3$ for odd $N$. No two members are adjacent, so $S_0$ is independent, and $|S_0| = m = \alpha(C_N)$. Since $S_t = S_0 + t$ and rotation by one seat is an automorphism of $C_N$ (it preserves adjacency), every $S_t$ is independent and maximum. $\blacksquare$

### 4.3 Lemma 2: every seat is scheduled at least once in every $P(N)$ consecutive shifts

$$P(N) = \begin{cases} 2 & N \text{ even} \\ 3 & N \text{ odd} \end{cases}$$

*Proof.* $p_i \in S_t$ iff $d = (i - t) \bmod N$ lies in $D = \{0, 2, \dots, 2(m-1)\}$. Each time $t$ increases by one, $d$ decreases by one (mod $N$).

- **Even $N$:** $D$ is all even residues, so $d$ alternates between $D$ and its complement. The seat is scheduled every 2 shifts.
- **Odd $N$:** $D = \{0, 2, \dots, N-3\}$ and the complement is $\{1, 3, \dots, N-2\} \cup \{N-1\}$. Counting downwards, the longest run of consecutive residues outside $D$ is $N-1, N-2$, which has length 2. So at most two consecutive shifts are unscheduled. $\blacksquare$

The bound is constant and independent of $N$. This schedule is the **fairness floor**. Every other mechanism is opportunism layered on top of it, and (as shown in §8) none can weaken it.

---

## 5. Sharded arbitration

### 5.1 Arcs and arbitrators

Seats are partitioned into contiguous **arcs** of size $k$ (the tuning parameter), giving $\lceil N/k \rceil$ arcs. Each arc has an **arbitrator**: a passive monitor (a lock plus a wait handle per philosopher), not a thread. The state of seat $j$ (its $\textsf{THINKING}/\textsf{HUNGRY}/\textsf{EATING}$ flag and the chopsticks it holds) is owned by the arbitrator of the arc containing $j$, written $a(j)$.

### 5.2 Lock neighbourhoods

An operation that reads or writes seats in a set $X$ acquires the lock of every arc that owns a seat in $X$, **always in ascending arc index**.

| Operation | Seats touched | Why |
|---|---|---|
| `request(i)` | $i-1,\ i,\ i+1$ | tests whether either neighbour is eating |
| `release(i)` | $i-2, \dots, i+2$ | may grant $p_{i\pm1}$, which needs *their* neighbours |
| shift boundary | all | global preempt, poll and backfill |

Philosophers in different parts of the table touch disjoint arcs and never contend. Only operations near an arc boundary (a **seam**) take two locks. A small $k$ means less contention per lock but more seams. A large $k$ means the reverse.

### 5.3 The ownership invariant

> **Invariant O.** The state of seat $j$ is read and written only while holding $a(j)$.

Every operation's neighbourhood includes every seat it reads or writes, so Invariant O holds by construction. One consequence: two concurrent operations that could grant adjacent philosophers $p_i$ and $p_{i+1}$ both hold $a(i)$ and $a(i+1)$, so they are serialised.

---

## 6. Opportunistic backfill

### 6.1 Why it is needed

A scheduled philosopher that is not hungry leaves its two chopsticks idle. Backfill recovers that capacity at the start of each shift.

### 6.2 Algorithm

After the scheduled grants, in a **single pass**:

```
backfill():
    candidates = { hungry, unscheduled seats with no eating neighbour }
    split candidates into maximal runs of consecutive seats
    for each run [s, s+1, ..., e]:
        if the run has even length:
            phase = parity of the earliest-arrived candidate's position in the run
        else:
            phase = 0
        grant s+phase, s+phase+2, s+phase+4, ...
```

Eligibility depends only on adjacency. Arrival order is used only as a tie-break, between the two equally large alternating phases of an even-length run. The pass must be done as a single step: deciding seat by seat independently could admit two adjacent candidates and break mutual exclusion.

### 6.3 Lemma 3: backfill is optimal

*Proof.* At least one seat is not a candidate, since $S_t \neq \varnothing$ and scheduled seats are never candidates. So the candidates induce a disjoint union of **paths** in $C_N$, not a cycle. Runs are separated by at least one non-candidate seat, so choices in different runs never conflict. A path on $L$ vertices has independence number $\lceil L/2 \rceil$, and alternating from one endpoint achieves it. For even $L$, both phases have size $L/2$. Choosing within each run optimally therefore gives a maximum independent subset of the candidates. Because a path has two ends, the wrap-around loss that costs a seat in $\lfloor N/2 \rfloor$ does not apply. $\blacksquare$

### 6.4 Scope of backfill

Because $S_t$ is a *maximum* independent set, every unscheduled seat is adjacent to at least one scheduled seat. In fact:

- **Even $N$:** unscheduled seats are pairwise non-adjacent, so every run has length 1. An unscheduled $p_i$ can be backfilled only when **both** scheduled neighbours $p_{i-1}$ and $p_{i+1}$ are idle.
- **Odd $N$:** the only adjacent unscheduled pair is the gap seats $\{t-2,\ t-1\}$, so runs have length at most 2.

So a single idle scheduled seat frees nothing. Capacity is recovered only where *consecutive* scheduled seats are idle. The gain is large under light load and close to zero under heavy load. The general run-based algorithm is kept because it stays correct if the schedule is ever replaced.

---

## 7. Bounded time slices

Each meal is capped at the slice length $\Delta$. A philosopher still eating at the boundary is **preempted**: its chopsticks are released, its remaining meal time is kept, and it becomes hungry again. It resumes at a later grant.

This does two jobs:

1. **It removes the convoy effect.** One slow eater cannot stretch a shift indefinitely, so every shift lasts at most $\Delta + \delta$, where $\delta$ is the time it takes diners to put chopsticks down.
2. **It makes the drain step finite.** At every boundary nobody is eating, so every hungry scheduled philosopher can be granted unconditionally.

Preemption is not needed to stop a philosopher monopolising a chopstick, because a meal ends on its own. It is needed specifically so that the shift boundary arrives on time.

---

## 8. Correctness

### Theorem 1 (Mutual exclusion)

*At every instant, the set $E$ of eating philosophers is an independent set of $C_N$. So no chopstick is held by two philosophers.*

*Proof.* By induction over the operations that add to $E$.

- **Poll.** After the drain, $E = \varnothing$, and the hungry members of $S_t$ form a subset of an independent set (Lemma 1).
- **Backfill.** Each candidate has no eating neighbour, alternation within a run never picks adjacent seats, and distinct runs are separated by a non-candidate (Lemma 3).
- **Opportunistic grant.** $p_i$ is granted only if $p_{i-1}$ and $p_{i+1}$ are not eating, tested while holding $a(i-1), a(i), a(i+1)$. By Invariant O no neighbour can start eating concurrently.

Since $E$ is independent, $\bigcup_{p \in E} R(p)$ is a disjoint union. $\blacksquare$

Mutual exclusion comes from **non-adjacency**. The count $\lfloor N/2 \rfloor$ is a consequence of independence, not its cause: a set of $\lfloor N/2 \rfloor$ adjacent philosophers would have the right size and double-allocate chopsticks.

### Theorem 2 (Deadlock freedom)

*Proof.* Deadlock needs all four Coffman conditions. This design breaks two of them, at two levels.

- **Chopsticks: no hold and wait.** Chopsticks are granted as an atomic pair inside the monitor, or not at all. No philosopher ever holds one chopstick while waiting for the other.
- **Arbitrator locks: no circular wait.** Every multi-lock acquisition is in ascending arc index. A cycle in the wait-for graph would need a strictly increasing sequence of indices that returns to its start, which is impossible in a finite totally ordered set.
- **No waiting under a lock.** Philosophers block on their own wait handle only *after* releasing all locks, and every critical section does a bounded amount of work.
- **The drain terminates.** Each preempted diner is woken by its preempt signal and calls `release`. The locks it needs are free because the clock released them before waiting. $\blacksquare$

### Theorem 3 (Starvation freedom with a constant bound)

*A philosopher that becomes hungry during shift $t$ is granted its chopsticks by the start of shift $t + P(N)$ at the latest. In real time, that is within $P(N)\cdot(\Delta + \delta)$.*

*Proof.* By Lemma 2 there is a shift $s$ with $t < s \le t + P(N)$ and $p_i \in S_s$. At the boundary into $s$, every diner has been preempted and drained (§7), so $E = \varnothing$, and the poll grants every hungry member of $S_s$ unconditionally. If $p_i$ was granted earlier, opportunistically or by backfill, that only helps. Each shift lasts at most $\Delta + \delta$. A preempted philosopher re-enters as hungry and the same argument applies to each remaining slice, so a meal of length $\mu$ completes within $\lceil \mu / \Delta \rceil$ grants, each bounded as above. $\blacksquare$

**Why opportunism cannot weaken the floor.** Every backfilled or opportunistic meal ends at the next boundary at the latest, so it can never run into the shift of a scheduled neighbour. The backfill rule is safe *because of* the preemption. The two mechanisms only work together.

### Theorem 4 (Concurrency)

Whenever every scheduled philosopher is hungry at a boundary, $|E| = \lfloor N/2 \rfloor = \alpha(C_N)$, the theoretical maximum. When some are not hungry, backfill recovers the idle capacity optimally within each run (Lemma 3). Sharding does not raise this ceiling, which is fixed by the number of chopsticks. It parallelises the *arbitration*, so independent permission decisions proceed at the same time instead of queueing on one global lock.

---

## 9. Complexity

| Operation | Locks taken | Work |
|---|---|---|
| `request` | arcs covering 3 seats: at most 2 when $k \ge 2$ | $O(1)$ |
| `release` | arcs covering 5 seats: at most 2 when $k \ge 4$ | $O(1)$ |
| shift boundary | all $\lceil N/k \rceil$ arcs | $O(N)$, once every $\Delta$ seconds |

---

## 10. Python implementation

The implementation uses only the standard library. Requires Python 3.10 or later.

**Notes on mapping the design to code:**

- `schedule`, `period_bound` and `backfill_choice` are pure functions. They are exactly the objects the lemmas are about, and they are tested on their own.
- `Arbitrator` is the per-arc monitor. `Table._locked` acquires the arc locks for a set of seats in ascending order.
- Each philosopher's wait handle is a `threading.Event`, a condition variable with a built-in flag, which avoids lost wake-ups. Philosophers wait on events only *after* leaving every lock.
- The invariants from §8 are checked at runtime with `check(...)`, which raises `InvariantViolation`. These checks use explicit raises rather than `assert` so that they still run under `python -O`.

The full implementation is in [`sharded_dining.py`](sharded_dining.py).

### 10.1 Tests

The tests are in [`test_sharded_dining.py`](test_sharded_dining.py) (run with `python -m pytest`).

What the tests establish:

| Test | Checks |
|---|---|
| `test_schedule_is_maximum_independent_and_fair` | Lemma 1 and Lemma 2 for $3 \le N \le 40$ |
| `test_backfill_is_optimal` | Lemma 3 against brute-force maximum independent sets for $3 \le N \le 10$ |
| `test_simulation_is_safe_and_starvation_free` | Theorems 1–3 in real threaded runs, including the degenerate arc sizes $k = 1$ and $k = N$ |

### 10.2 Results

All 53 tests pass. A further stress run of 240 simulations (seeds 0–39 × $N \in \{3, 5, 8, 11, 16\}$, 3 ms slices) raised no invariant violations. As a negative control, replacing `schedule` with one that schedules two *adjacent* seats is caught straight away (`InvariantViolation: scheduled set not independent`), so the runtime checks are not vacuous.

Sample run (`python sharded_dining.py`, $N = 7$, $k = 3$):

```
               n: 7
          shifts: 20
           meals: [6, 6, 6, 6, 6, 6, 6]
     preemptions: [6, 7, 6, 2, 6, 4, 7]
 max_wait_shifts: [3, 2, 2, 2, 2, 2, 2]
      wait_bound: 3
          grants: {'opportunistic': 28, 'scheduled': 45, 'backfill': 7}
  max_concurrent: 3
         ceiling: 3
```

Every philosopher finished, the worst wait was exactly the odd-$N$ bound of 3 shifts, concurrency reached the ceiling $\lfloor 7/2 \rfloor = 3$, and 35 of the 80 grants (44%) came from backfill or opportunism. Those are meals a pure rotation would have left on the table.

---

## 11. Limitations

1. **Backfill is limited by construction.** A single idle scheduled seat cannot be backfilled (§6.4). Only runs of idle seats free capacity.
2. **Preemption has a cost.** Partial-meal state must be kept, and each preemption is a context switch, which is time not spent eating. A small $\Delta$ tightens the real-time bound but increases preemptions.
3. **The boundary is a global step.** Advancing the shift takes every arc lock. Sharding parallelises requests and releases, not the boundary, which costs $O(N)$ once per $\Delta$. A distributed barrier could replace it at the cost of a harder proof.
4. **Seams are the error-prone part.** Correctness near arc boundaries depends entirely on the ascending lock order and on the release neighbourhood having radius 2.
5. **Symmetry.** Identical deterministic processes in identical states cannot break a symmetric tie (Lehmann & Rabin, 1981). This design relies on seat indices, an asymmetry already present in the problem, for both the schedule and the lock order.

---

## 12. Comparison with alternatives

| | Fairness | Parallelism | Scalability | Ease of proof |
|---|---|---|---|---|
| Resource ordering (Dijkstra) | None: starvation possible | Maximal, not maximum | Good | High |
| Single arbitrator / monitor | Bounded overtaking | Maximal, not maximum | Poor: one global lock | Highest |
| Distributed rotation | Constant shift bound | $\lfloor N/2 \rfloor$ floor | Best: no arbitrator | Lowest |
| **This design** | **Constant real-time bound** | $\lfloor N/2 \rfloor$ **plus optimal backfill** | **Tunable via arc size** | Moderate |

The single arbitrator is the easiest to prove correct and does not scale. Fully distributed rotation scales best and is the easiest to get subtly wrong. This design sits between them: sharding recovers most of the distributed version's scalability, and each arc keeps a monitor-based correctness argument. It is the only one of these whose starvation bound is stated in **real time** rather than in scheduling rounds.

---

## References

- E. W. Dijkstra. *Hierarchical ordering of sequential processes.* Acta Informatica 1(2), 1971.
- C. A. R. Hoare. *Monitors: an operating system structuring concept.* Communications of the ACM 17(10), 1974.
- E. G. Coffman, M. Elphick, A. Shoshani. *System deadlocks.* ACM Computing Surveys 3(2), 1971.
- D. Lehmann, M. O. Rabin. *On the advantages of free choice: a symmetric and fully distributed solution to the dining philosophers problem.* POPL, 1981.
- K. M. Chandy, J. Misra. *The drinking philosophers problem.* ACM TOPLAS 6(4), 1984.
- A. S. Tanenbaum, H. Bos. *Modern Operating Systems*, 4th ed. Pearson, 2014.

---

<sub>Disclaimer Note: AI assistance was used to help write the tests as well as formally write up the README.md based on the context of my own notes, working out, and solution.</sub>
