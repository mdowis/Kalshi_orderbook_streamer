"""In-memory orderbook state: applies Kalshi WebSocket snapshots and deltas."""

from __future__ import annotations

import time


class OrderbookState:
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.seq: int = 0
        # price (int, in cents) → size (int, number of contracts)
        self.yes: dict[int, int] = {}
        self.no: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply_snapshot(self, msg: dict) -> None:
        """Replace full orderbook state from a snapshot message."""
        self.seq = msg.get("seq", 0)
        self.yes = {int(p): int(s) for p, s in msg.get("yes", [])}
        self.no = {int(p): int(s) for p, s in msg.get("no", [])}

    def apply_delta(self, msg: dict) -> None:
        """Apply a single price-level delta.

        Kalshi sends the *change* in quantity (positive = more contracts,
        negative = fewer).  A level that reaches 0 or below is removed.
        """
        self.seq = msg.get("seq", self.seq + 1)
        side: str = msg["side"]
        price = int(msg["price"])
        delta = int(msg["delta"])

        levels = self.yes if side == "yes" else self.no
        new_size = levels.get(price, 0) + delta
        if new_size <= 0:
            levels.pop(price, None)
        else:
            levels[price] = new_size

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _sorted_yes(self) -> list[list[int]]:
        """YES bids sorted best (highest price) first."""
        return [[p, s] for p, s in sorted(self.yes.items(), reverse=True)]

    def _sorted_no(self) -> list[list[int]]:
        """NO bids sorted best (highest price) first."""
        return [[p, s] for p, s in sorted(self.no.items(), reverse=True)]

    def to_snapshot_record(self, ts: float | None = None) -> dict:
        return {
            "type": "snapshot",
            "ts": ts if ts is not None else time.time(),
            "ticker": self.ticker,
            "seq": self.seq,
            "yes": self._sorted_yes(),
            "no": self._sorted_no(),
        }

    @staticmethod
    def to_delta_record(ticker: str, msg: dict, ts: float | None = None) -> dict:
        return {
            "type": "delta",
            "ts": ts if ts is not None else time.time(),
            "ticker": ticker,
            "seq": msg.get("seq"),
            "side": msg["side"],
            "price": int(msg["price"]),
            "delta": int(msg["delta"]),
        }
