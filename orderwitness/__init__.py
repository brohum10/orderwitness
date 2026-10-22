"""Bounded linearizability checking for completed scalar register histories."""

from .model import History
from .search import check
from .certificate import verify

__all__ = ["History", "check", "verify"]
__version__ = "0.1.0"
