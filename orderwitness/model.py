"""Strict, immutable input for completed operations on scalar-valued registers."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 1_000_000
MAX_OPERATIONS = 32
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON numbers are unsupported")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def name(value):
    return type(value) is str and NAME.fullmatch(value) is not None


def scalar(value):
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return -(2**63) <= value < 2**63
    return type(value) is str and len(value) <= 1024


@dataclass(frozen=True)
class History:
    encoded: str

    @classmethod
    def from_document(cls, document):
        if type(document) is not dict or not {"version", "operations"} <= set(document):
            raise ValueError("history needs version and operations")
        if set(document) - {"version", "initial", "operations"}:
            raise ValueError("unsupported history field")
        if type(document["version"]) is not int or document["version"] != 1:
            raise ValueError("version must be integer 1")
        initial = document.get("initial", {})
        if type(initial) is not dict or any(
            not name(k) or not scalar(v) for k, v in initial.items()
        ):
            raise ValueError("initial maps valid key names to supported scalar values")
        operations = document["operations"]
        if type(operations) is not list or len(operations) > MAX_OPERATIONS:
            raise ValueError(
                f"operations must be a list of at most {MAX_OPERATIONS} completed operations"
            )
        ids, positions, keys, clients = set(), set(), set(initial), {}
        common = {"id", "client", "start", "end", "op", "key", "result"}
        for op in operations:
            if type(op) is not dict or not common <= set(op):
                raise ValueError(
                    "every completed operation needs id/client/start/end/op/key/result"
                )
            kind = op["op"]
            if type(kind) is not str or kind not in {"get", "put", "cas"}:
                raise ValueError("supported operations: get, put, cas")
            extra = (
                {"value"} if kind == "put" else {"expected", "value"} if kind == "cas" else set()
            )
            if set(op) != common | extra:
                raise ValueError(f"incorrect fields for {kind}")
            if any(not name(op[field]) for field in ["id", "client", "key"]):
                raise ValueError("invalid id/client/key")
            if op["id"] in ids:
                raise ValueError("duplicate operation id")
            ids.add(op["id"])
            start, end = op["start"], op["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end < 2**63:
                raise ValueError(
                    "start/end must be nonnegative integer event positions with start < end"
                )
            if start in positions or end in positions:
                raise ValueError("all invocation/response positions must be globally unique")
            positions.update([start, end])
            keys.add(op["key"])
            clients.setdefault(op["client"], []).append((start, end))
            if kind == "get" and not scalar(op["result"]):
                raise ValueError("get result must be a supported scalar")
            if kind == "put" and op["result"] is not None:
                raise ValueError("put acknowledgement must be null")
            if kind == "cas" and (not scalar(op["expected"]) or type(op["result"]) is not bool):
                raise ValueError("cas needs a scalar expected value and a boolean success result")
            if kind in {"put", "cas"} and not scalar(op["value"]):
                raise ValueError("new value must be a supported scalar")
        if len(keys) > 32:
            raise ValueError("at most 32 keys are supported")
        for intervals in clients.values():
            ordered = sorted(intervals)
            if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
                raise ValueError("a sequential client cannot have overlapping calls")
        normalized = {
            "version": 1,
            "initial": initial,
            "operations": sorted(operations, key=lambda row: row["id"]),
        }
        encoded = canonical(normalized)
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError("canonical history exceeds byte limit")
        return cls(encoded)

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("history exceeds byte limit")
        return cls.from_document(strict_json(data.decode("utf-8")))

    def document(self):
        return json.loads(self.encoded)

    @property
    def sha256(self):
        return hashlib.sha256(self.encoded.encode()).hexdigest()
