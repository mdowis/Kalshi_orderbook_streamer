"""Continuously stream Kalshi orderbook data for BTC and ETH 15-min contracts.

Connects to the Kalshi WebSocket, subscribes to orderbook_delta for all open
KXBTC15M and KXETH15M markets, applies snapshot + delta messages to maintain
live orderbook state, and persists every event to JSONL files.  Commits data
to the GitHub repo on each market close and every COMMIT_INTERVAL seconds.

Environment variables:
  KALSHI_API_KEY_ID       – API key ID from Kalshi account settings
  KALSHI_PRIVATE_KEY      – Full PEM content of the RSA private key
  STREAM_DURATION_SECONDS – How long to run before exiting (default 21000)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import websockets
import websockets.exceptions

# Allow running as `python scripts/stream_orderbook.py` from repo root.
sys.path.insert(0, os.path.dirname(__file__))
from github_storage import append_record, commit_data
from kalshi_auth import get_auth_headers
from market_discovery import get_active_tickers
from orderbook_state import OrderbookState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
STREAM_DURATION = int(os.environ.get("STREAM_DURATION_SECONDS", "21000"))
COMMIT_INTERVAL = 60  # seconds between periodic commits
DISCOVERY_RETRY_WAIT = 30  # seconds to wait if no open markets found


async def _subscribe(ws, tickers: list[str], msg_id: int) -> int:
    msg_id += 1
    await ws.send(json.dumps({
        "id": msg_id,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "market_lifecycle_v2"],
            "market_tickers": tickers,
        },
    }))
    log.info("Subscribed to %d markets: %s", len(tickers), tickers)
    return msg_id


async def _stream_session(
    orderbooks: dict[str, OrderbookState],
    start_time: float,
    last_commit_ref: list[float],  # mutable single-element list for closure sharing
    msg_id_ref: list[int],
) -> None:
    """Open one WebSocket session and process messages until duration or error."""
    headers = get_auth_headers("GET", WS_PATH)

    async with websockets.connect(
        WS_URL,
        additional_headers=headers,
        ping_interval=30,
        ping_timeout=15,
        close_timeout=5,
    ) as ws:
        tickers = get_active_tickers()
        if not tickers:
            log.warning("No open markets found; waiting %ds.", DISCOVERY_RETRY_WAIT)
            await asyncio.sleep(DISCOVERY_RETRY_WAIT)
            return

        msg_id_ref[0] = await _subscribe(ws, tickers, msg_id_ref[0])
        for t in tickers:
            orderbooks.setdefault(t, OrderbookState(t))

        async for raw in ws:
            now = time.time()

            if time.monotonic() - start_time >= STREAM_DURATION:
                log.info("Stream duration reached; stopping.")
                return

            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON message: %.120s", raw)
                continue

            msg_type: str = envelope.get("type", "")
            data: dict = envelope.get("msg", {})
            ticker: str = data.get("market_ticker", "")

            # ── Orderbook events ──────────────────────────────────────────
            if msg_type == "orderbook_delta" and ticker:
                ob = orderbooks.setdefault(ticker, OrderbookState(ticker))
                sub_type: str = data.get("type", "")

                if sub_type == "snapshot":
                    ob.apply_snapshot(data)
                    record = ob.to_snapshot_record(now)
                    append_record(ticker, record, now)
                    log.debug("Snapshot %s seq=%d", ticker, ob.seq)

                elif sub_type == "delta":
                    ob.apply_delta(data)
                    record = OrderbookState.to_delta_record(ticker, data, now)
                    append_record(ticker, record, now)

            # ── Market lifecycle events ───────────────────────────────────
            elif msg_type == "market_lifecycle_v2" and ticker:
                status: str = data.get("status", "")
                log.info("Market %s → %s", ticker, status)

                if status in ("closed", "settled"):
                    dt = datetime.fromtimestamp(now, tz=timezone.utc)
                    commit_data(
                        f"data: {ticker} {status} {dt.strftime('%Y-%m-%d %H:%M')} UTC"
                    )
                    last_commit_ref[0] = time.monotonic()

                    # Subscribe to any newly opened markets.
                    new_tickers = [
                        t for t in get_active_tickers() if t not in orderbooks
                    ]
                    if new_tickers:
                        msg_id_ref[0] = await _subscribe(ws, new_tickers, msg_id_ref[0])
                        for t in new_tickers:
                            orderbooks[t] = OrderbookState(t)

            # ── Periodic commit ───────────────────────────────────────────
            if time.monotonic() - last_commit_ref[0] >= COMMIT_INTERVAL:
                dt = datetime.fromtimestamp(now, tz=timezone.utc)
                commit_data(f"data: periodic {dt.strftime('%Y-%m-%d %H:%M')} UTC")
                last_commit_ref[0] = time.monotonic()


async def run() -> None:
    start_time = time.monotonic()
    orderbooks: dict[str, OrderbookState] = {}
    last_commit_ref = [time.monotonic()]
    msg_id_ref = [0]
    backoff = 2.0

    while time.monotonic() - start_time < STREAM_DURATION:
        try:
            await _stream_session(orderbooks, start_time, last_commit_ref, msg_id_ref)
            backoff = 2.0  # reset after a clean session
        except websockets.exceptions.ConnectionClosed as exc:
            log.warning("Connection closed (%s); reconnecting in %.0fs.", exc, backoff)
        except Exception as exc:
            log.exception("Unexpected error — reconnecting in %.0fs.", backoff)

        remaining = STREAM_DURATION - (time.monotonic() - start_time)
        if remaining <= 0:
            break
        await asyncio.sleep(min(backoff, remaining))
        backoff = min(backoff * 2, 60.0)

    log.info("Streamer finished. Total duration: %.0fs.", time.monotonic() - start_time)


if __name__ == "__main__":
    log.info("Starting streamer — duration=%ds commit_interval=%ds", STREAM_DURATION, COMMIT_INTERVAL)
    asyncio.run(run())
