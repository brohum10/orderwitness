# OrderWitness

[![Checks](https://github.com/brohum10/orderwitness/actions/workflows/ci.yml/badge.svg)](https://github.com/brohum10/orderwitness/actions/workflows/ci.yml)

**Two clients both say they reserved the last seat. Could that history have behaved atomically?**

OrderWitness checks small concurrent read/write/compare-and-swap histories. It
searches for a sequential explanation that respects when operations actually
started and finished. If one exists, it returns an operation order that a
separate verifier can replay. If search runs out of budget, it says **UNKNOWN**,
not “your system is broken.”

Python 3.12+ · zero runtime dependencies · offline CLI · MIT

## The demo: finishing first does not mean taking effect first

Both clients try changing `seat` from `0` to their own value:

```text
Ordered collector event:  0    1    2    3    4    5    6
winner: CAS 0 -> 1        [------------------] true
loser:  CAS 0 -> 2             [----] false
observe: GET                                      [----] 1

Legal witness: winner -> loser -> observe
```

The losing call returns first, but overlaps the winning call. The winner can
take effect inside that overlap, explaining the loser's failure. Sorting by
completion time would incorrectly reject this valid history.

Change just the loser's result to `true` and **no legal order exists**: both CAS
calls cannot successfully replace the same initial value without an intervening
reset. The two checked-in fixtures demonstrate exactly that distinction.

## Run in under a minute

Clone the repository, open a terminal **in its folder**, and run:

```sh
python -m orderwitness check examples/overlap-pass.json --format text
python -m orderwitness check examples/double-cas-fail.json --format text
python -m orderwitness check examples/stale-read-fail.json --format text
python -m orderwitness check examples/overlap-pass.json --max-states 1 --format text
```

Use `python3` instead of `python` if that is your Python 3.12+ command.
The last three commands intentionally return nonzero exit codes.

Default output is machine-readable JSON. Save and independently verify a
positive result (choose a new output filename to avoid overwriting your files):

```sh
python -m orderwitness check examples/overlap-pass.json > witness.json
python -m orderwitness verify examples/overlap-pass.json witness.json
```

Optional installation: `python -m pip install .`, then use `orderwitness` in place
of `python -m orderwitness`. Installation needs setuptools for packaging; running
directly from the checkout needs only the standard library.

| Exit | Meaning |
| --- | --- |
| 0 | A legal witness exists, or a positive certificate verified |
| 1 | Exhaustive search found no legal order for this input/model |
| 2 | Invalid input, invalid certificate, or file/argument error |
| 3 | State budget exhausted; consistency is unknown |

## Input contract

```json
{
  "version": 1,
  "initial": {"seat": 0},
  "operations": [
    {"id": "a", "client": "one", "start": 0, "end": 1,
     "op": "cas", "key": "seat", "expected": 0, "value": 1, "result": true}
  ]
}
```

- `start` and `end` are **unique integer positions in one globally ordered
  invocation/response event log**. They are not timestamps from unsynchronized
  machines. Every call has `start < end`.
- Completed operations only. Missing responses are rejected, never silently
  dropped. Calls by the same sequential `client` cannot overlap.
- `get`: its `result` must equal the register's current value.
- `put`: write `value`; its acknowledgement `result` must be `null`.
- `cas`: compare with `expected`, write `value` only on equality, and return a
  boolean success `result`. A failed CAS does not change the register.
- Values: null, boolean, signed 64-bit integer, or string up to 1,024 characters.
  Equality is type-sensitive: `true`, `1`, and `"1"` are different.
- Unspecified keys initially contain `null`. Initial and accessed keys appear
  in the final state. IDs, clients, and keys use `[A-Za-z0-9_.:-]{1,128}`.
- At most 32 completed operations and 32 keys; at most 1 MB of input.
  Duplicate JSON keys, unknown fields, floats, and non-finite numbers are rejected.

**Capture integration:** instrument request invocation and response at a
collector that assigns one monotonic event sequence. Record the observed return
value and the exact initial state. Multi-host wall clocks, incomplete captures,
retries, timeouts, and transactions require additional modeling; this tool does
not infer those semantics. The examples are synthetic, not production captures.

## How it works

```text
JSON -> strict validation + normalized history hash
     -> real-time predecessor bitmasks
     -> bounded DFS over eligible operations and register states
        -> found order -> independent replay verifier -> positive witness
        -> all branches exhausted -> NONLINEARIZABLE
        -> state budget exhausted -> UNKNOWN
```

An operation that completed before another began must come first. Overlapping
operations can be explored in either order, provided their returned values fit
the model. The dead-state cache keys on **both** completed-operation bitmask and
full register state: the same operations in different orders can leave different
values. Caching only the bitmask would be unsound; a regression test covers it.

Operations are searched in ID order for deterministic results, independent of
input row order. Up to eight rejected prefixes are included as illustrative
dead ends, **not** a minimal counterexample or a global explanation of failure.

`certificate.py` does not import the search transitions or predecessor masks.
It independently checks each operation appears once, every real-time constraint,
all return values, the final state, and the SHA-256 of the normalized input.
Only positive results have replay certificates. A negative result is an
exhaustive-search result, not an independently certified impossibility proof.

## Reproduce the evidence

```sh
python -m unittest discover -s tests -v
python scripts/verify_reference.py
```

The suite contains 37 tests, including **400 seeded histories of up to six
operations checked against a separate exhaustive-permutation oracle**. It also
covers certificate tampering, typed equality, input limits, CLI exit codes,
real-time ordering, memoization, and budget exhaustion. These are software checks,
not a formal proof of the checker.

`evidence/reference.json` contains complete deterministic reports for five
scenarios. The verification script recomputes all reports and compares every
field, including search counters. Its synthetic eight-overlapping-writes case
exercises dead-state reuse. Counters describe those fixtures only; they are not
latency claims or production throughput measurements. CI repeats the tests,
evidence checks, and package installation on Linux/Windows with Python 3.12/3.14.

| Reproducible scenario | Result | Expanded states | Dead-state cache hits |
| --- | --- | ---: | ---: |
| Overlapping CAS, one winner | Linearizable | 4 | 0 |
| Overlapping CAS, two reported winners | Nonlinearizable | 3 | 0 |
| Stale read after a completed write | Nonlinearizable | 2 | 0 |
| Valid overlap with a one-state budget | Unknown | 1 | 0 |
| Eight overlapping writes, impossible later read | Nonlinearizable | 256 | 769 |

The last case has 40,320 possible write permutations. Shared dead states avoid
re-exploring equivalent prefixes; that fixture-specific reduction is not a
general speedup guarantee.

## Limits and next steps

This is a bounded diagnostic for independent scalar registers, not a general
distributed database verifier. It does not model pending operations, queues,
transactions, durability, liveness, or exactly-once network execution. A passing
history says nothing about executions that were not captured. Search is
exponential in the worst case; the default 50,000-state budget is not a hard
memory or time limit. See [SECURITY.md](SECURITY.md).

Useful extensions would be collector adapters, richer object models, and
partition-aware search. They are not implemented or claimed here.

The underlying correctness concept comes from Herlihy and Wing's
[Linearizability: A Correctness Condition for Concurrent Objects (1990)](https://www.cs.cmu.edu/~wing/publications/HerlihyWing90.pdf).
This repository is an educational implementation of that established idea,
with a deliberately narrow model and explicit evidence boundaries.
