"""Bounded, memoized search of legal real-time-respecting sequential orders."""

import json

from .model import History, canonical


class SearchLimit(Exception):
    pass


def check(history: History, *, max_states=50_000):
    if type(max_states) is not int or not 1 <= max_states <= 1_000_000:
        raise ValueError("max_states must be an integer from 1 to 1000000")
    document = history.document()
    operations = document["operations"]
    keys = sorted(set(document["initial"]) | {op["key"] for op in operations})
    slots = {key: i for i, key in enumerate(keys)}
    initial = tuple(canonical(document["initial"].get(key)) for key in keys)
    predecessors = [
        sum(1 << j for j, prior in enumerate(operations) if prior["end"] < op["start"])
        for op in operations
    ]
    all_done = (1 << len(operations)) - 1
    dead, path, rejected = set(), [], []
    stats = {
        "states_explored": 0,
        "cache_hits": 0,
        "transitions_considered": 0,
        "inconsistent_returns": 0,
        "dead_states": 0,
        "real_time_edges": sum(mask.bit_count() for mask in predecessors),
    }

    def advance(state, operation):
        slot = slots[operation["key"]]
        current = state[slot]
        if operation["op"] == "get":
            return state if current == canonical(operation["result"]) else None
        if operation["op"] == "cas":
            matches = current == canonical(operation["expected"])
            if matches != operation["result"]:
                return None
            if not matches:
                return state
        updated = list(state)
        updated[slot] = canonical(operation["value"])
        return tuple(updated)

    def search(done, state):
        cache_key = (done, state)
        if cache_key in dead:
            stats["cache_hits"] += 1
            return None
        if stats["states_explored"] >= max_states:
            raise SearchLimit
        stats["states_explored"] += 1
        if done == all_done:
            return tuple(path), state
        for i, operation in enumerate(operations):
            if done & (1 << i) or predecessors[i] & ~done:
                continue
            stats["transitions_considered"] += 1
            next_state = advance(state, operation)
            if next_state is None:
                stats["inconsistent_returns"] += 1
                if len(rejected) < 8:
                    rejected.append(
                        {
                            "prefix": list(path),
                            "operation": operation["id"],
                            "observed_result": operation["result"],
                            "model_value_before": json.loads(state[slots[operation["key"]]]),
                        }
                    )
                continue
            path.append(operation["id"])
            result = search(done | (1 << i), next_state)
            path.pop()
            if result is not None:
                return result
        dead.add(cache_key)
        stats["dead_states"] = len(dead)
        return None

    result, exhausted = None, False
    try:
        result = search(0, initial)
    except SearchLimit:
        exhausted = True
    return {
        "version": 1,
        "status": (
            "unknown" if exhausted else "linearizable" if result is not None else "nonlinearizable"
        ),
        "history_sha256": history.sha256,
        "operation_count": len(operations),
        "max_states": max_states,
        "witness": list(result[0]) if result is not None else None,
        "final_state": (
            {key: json.loads(value) for key, value in zip(keys, result[1])}
            if result is not None
            else None
        ),
        "stats": stats,
        "rejected_prefix_examples": rejected,
        "scope": "completed get/put/cas operations; one globally ordered invocation/response log",
    }
