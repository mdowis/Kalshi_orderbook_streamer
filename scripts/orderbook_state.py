"""In-memory orderbook state: applies Kalshi WebSocket snapshots and deltas.

As of March 2026 Kalshi uses fixed-point dollar strings for prices and sizes.
Snapshots arrive as type "orderbook_snapshot"; deltas as "orderbook_delta".
The seq number lives at the envelope level, not inside msg.
"""

from __future__ import annotations

import time


class OrderbookState:
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.seq: int = 0
        # price_str (dollar string e.g. "0.5500") → size_str (dollar string e.g. "150.00")
        self.yes: dict[str, str] = {}
        self.no: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply_snapshot(self, seq: int, msg: dict) -> None:
        """Replace full state from a snapshot message's msg payload."""
        self.seq = seq
        self.yes = {p: s for p, s in msg.get("yes_dollars_fp", [])}
        self.no  = {p: s for p, s in msg.get("no_dollars_fp",  [])}

    def apply_delta(self, seq: int, msg: dict) -> None:
        """Apply a single price-level delta.

        Expects msg to contain: side, price (dollar string), delta (dollar string).
        A level whose new size reaches 0 or below is removed.
        """
        self.seq = seq
        side  = msg.get("side", "")
        price = msg.get("price", "")
        delta = float(msg.get("delta", 0))

        levels = self.yes if side == "yes" else self.no
        new_size = float(levels.get(price, "0")) + delta
        if new_size <= 0:
            levels.pop(price, None)
        else:
            levels[price] = f"{new_size:.2f}"

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _sorted_yes(self) -> list[list[str]]:
        """YES bids sorted best (highest price) first."""
        return [[p, s] for p, s in sorted(
            self.yes.items(), key=lambda x: float(x[0]), reverse=True
        )]

    def _sorted_no(self) -> list[list[str]]:
        """NO bids sorted best (highest price) first."""
        return [[p, s] for p, s in sorted(
            self.no.items(), key=lambda x: float(x[0]), reverse=True
        )]

    def to_snapshot_record(self, ts: float | None = None) -> dict:
        return {
            "type": "snapshot",
            "ts":   ts if ts is not None else time.time(),
            "ticker": self.ticker,
            "seq":  self.seq,
            "yes":  self._sorted_yes(),
            "no":   self._sorted_no(),
        }

    @staticmethod
    def to_delta_record(ticker: str, seq: int, msg: dict, ts: float | None = None) -> dict:
        record = {
            "type":   "delta",
            "ts":     ts if ts is not None else time.time(),
            "ticker": ticker,
            "seq":    seq,
        }
        # Forward delta fields (side, price, delta) as-is.
        for key in ("side", "price", "delta"):
            if key in msg:
                record[key] = msg[key]
        return record
