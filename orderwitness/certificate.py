"""Independent replay verifier for positive search witnesses.

This deliberately does not import the search model or its predecessor masks.
Negative and budget-limited results are not claimed to be proof certificates.
"""

from .model import History


def same_value(a, b):
    return type(a) is type(b) and a == b


def verify(history: History, report):
    if (
        type(report) is not dict
        or type(report.get("version")) is not int
        or report.get("version") != 1
    ):
        raise ValueError("invalid report version")
    if report.get("status") != "linearizable" or report.get("history_sha256") != history.sha256:
        raise ValueError("certificate must be positive and match the exact normalized history")
    document = history.document()
    operations = {op["id"]: op for op in document["operations"]}
    order = report.get("witness")
    if (
        type(order) is not list
        or any(type(key) is not str for key in order)
        or len(order) != len(operations)
        or set(order) != set(operations)
    ):
        raise ValueError("witness must contain each operation exactly once")
    rank = {key: i for i, key in enumerate(order)}
    for left in operations.values():
        for right in operations.values():
            if left["end"] < right["start"] and rank[left["id"]] >= rank[right["id"]]:
                raise ValueError("witness violates real-time order")
    state = dict(document["initial"])
    for op in operations.values():
        state.setdefault(op["key"], None)
    for key in order:
        op = operations[key]
        before = state[op["key"]]
        if op["op"] == "get":
            if not same_value(before, op["result"]):
                raise ValueError("get result disagrees with replay")
        elif op["op"] == "put":
            state[op["key"]] = op["value"]
        else:
            matches = same_value(before, op["expected"])
            if matches is not op["result"]:
                raise ValueError("CAS success result disagrees with replay")
            if matches:
                state[op["key"]] = op["value"]
    final = report.get("final_state")
    if type(final) is not dict or set(final) != set(state):
        raise ValueError("incorrect final-state keys")
    if any(not same_value(state[key], final[key]) for key in state):
        raise ValueError("incorrect final-state value")
    return True
