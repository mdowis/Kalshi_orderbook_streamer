"""Continuously stream Kalshi orderbook data for BTC and ETH 15-min contracts.

Connects to the Kalshi WebSocket, subscribes to orderbook_delta, trade, ticker,
and market_lifecycle_v2 for all open KXBTC15M and KXETH15M markets, applies
snapshot + delta messages to maintain live orderbook state, and persists every
event to JSONL files.  Commits data to the GitHub repo on each market close and
every COMMIT_INTERVAL seconds.

Also fetches and persists market metadata (strike, expiry, etc.) at subscription
time and periodic REST stats snapshots (volume, OI, last price) every
STATS_INTERVAL seconds.

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
from market_discovery import get_active_tickers, get_market_metadata, get_market_stats
from orderbook_state import OrderbookState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
STREAM_DURATION  = int(os.environ.get("STREAM_DURATION_SECONDS", "21000"))
COMMIT_INTERVAL  = 60   # seconds between periodic git commits
STATS_INTERVAL   = 300  # seconds between REST market-stats snapshots
DISCOVERY_RETRY_WAIT = 30  # seconds to wait if no open markets found


async def _subscribe(ws, tickers: list[str], msg_id: int) -> int:
    msg_id += 1
    await ws.send(json.dumps({
        "id": msg_id,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "trade", "ticker", "market_lifecycle_v2"],
            "market_tickers": tickers,
        },
    }))
    log.info("Subscribed to %d markets: %s", len(tickers), tickers)
    return msg_id


def _save_metadata(tickers: list[str]) -> None:
    """Fetch and persist market metadata for each newly subscribed ticker."""
    for ticker in tickers:
        meta = get_market_metadata(ticker)
        append_record(ticker, meta, time.time())
        log.info("Metadata saved for %s: floor=%s cap=%s close=%s",
                 ticker,
                 meta.get("floor_strike"), meta.get("cap_strike"),
                 meta.get("close_time"))


def _save_stats(tickers: list[str]) -> None:
    """Fetch and persist REST market stats for each active ticker."""
    for ticker in tickers:
        stats = get_market_stats(ticker)
        if stats:
            append_record(ticker, stats, stats["ts"])


async def _stream_session(
    orderbooks: dict[str, OrderbookState],
    start_time: float,
    last_commit_ref: list[float],
    last_stats_ref: list[float],
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

        new_tickers = [t for t in tickers if t not in orderbooks]
        msg_id_ref[0] = await _subscribe(ws, tickers, msg_id_ref[0])
        if new_tickers:
            _save_metadata(new_tickers)
        for t in tickers:
            orderbooks.setdefault(t, OrderbookState(t))

        raw_logged = 0  # log first few raw messages to verify wire format

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

            # Log first 5 messages verbatim to verify wire format.
            if raw_logged < 5:
                log.info("RAW[%d] %s", raw_logged, json.dumps(envelope)[:500])
                raw_logged += 1

            msg_type: str = envelope.get("type", "")
            seq: int      = envelope.get("seq", 0)
            data: dict    = envelope.get("msg", {})
            ticker: str   = data.get("market_ticker", "")

            # ── Initial orderbook snapshot ────────────────────────────────
            if msg_type == "orderbook_snapshot" and ticker:
                ob = orderbooks.setdefault(ticker, OrderbookState(ticker))
                ob.apply_snapshot(seq, data)
                record = ob.to_snapshot_record(now)
                append_record(ticker, record, now)
                log.info("Snapshot %s seq=%d yes_levels=%d no_levels=%d",
                         ticker, seq, len(ob.yes), len(ob.no))

            # ── Incremental orderbook delta ───────────────────────────────
            elif msg_type == "orderbook_delta" and ticker:
                ob = orderbooks.setdefault(ticker, OrderbookState(ticker))
                ob.apply_delta(seq, data)
                record = OrderbookState.to_delta_record(ticker, seq, data, now)
                append_record(ticker, record, now)

            # ── Executed trade ────────────────────────────────────────────
            elif msg_type == "trade" and ticker:
                if ticker in orderbooks:
                    record = {
                        "type":   "trade",
                        "ts":     now,
                        "ticker": ticker,
                        "seq":    seq,
                    }
                    for key, val in data.items():
                        if key != "market_ticker":
                            record[key] = val
                    append_record(ticker, record, now)

            # ── Real-time ticker update (price, volume, OI) ───────────────
            elif msg_type == "ticker" and ticker:
                if ticker in orderbooks:
                    record = {
                        "type":   "ticker",
                        "ts":     now,
                        "ticker": ticker,
                        "seq":    seq,
                    }
                    for key, val in data.items():
                        if key != "market_ticker":
                            record[key] = val
                    append_record(ticker, record, now)

            # ── Market lifecycle events ───────────────────────────────────
            elif msg_type == "market_lifecycle_v2" and ticker:
                event_type: str = data.get("event_type", "")
                if ticker not in orderbooks:
                    continue  # platform-wide broadcast — ignore other markets
                log.info("Market lifecycle %s event_type=%s", ticker, event_type)

                if event_type == "determined":
                    dt = datetime.fromtimestamp(now, tz=timezone.utc)
                    commit_data(
                        f"data: {ticker} settled {dt.strftime('%Y-%m-%d %H:%M')} UTC"
                    )
                    last_commit_ref[0] = time.monotonic()

                    new_tickers = [
                        t for t in get_active_tickers() if t not in orderbooks
                    ]
                    if new_tickers:
                        msg_id_ref[0] = await _subscribe(ws, new_tickers, msg_id_ref[0])
                        _save_metadata(new_tickers)
                        for t in new_tickers:
                            orderbooks[t] = OrderbookState(t)

            # ── Periodic REST stats snapshot ──────────────────────────────
            if time.monotonic() - last_stats_ref[0] >= STATS_INTERVAL:
                active = list(orderbooks.keys())
                log.info("Fetching REST stats for %d tickers.", len(active))
                _save_stats(active)
                last_stats_ref[0] = time.monotonic()

            # ── Periodic commit ───────────────────────────────────────────
            if time.monotonic() - last_commit_ref[0] >= COMMIT_INTERVAL:
                dt = datetime.fromtimestamp(now, tz=timezone.utc)
                label = f"data: periodic {dt.strftime('%Y-%m-%d %H:%M')} UTC"
                log.info("Triggering periodic commit: %s", label)
                commit_data(label)
                last_commit_ref[0] = time.monotonic()


async def run() -> None:
    start_time = time.monotonic()
    orderbooks: dict[str, OrderbookState] = {}
    last_commit_ref = [time.monotonic()]
    last_stats_ref  = [time.monotonic()]
    msg_id_ref = [0]
    backoff = 2.0

    while time.monotonic() - start_time < STREAM_DURATION:
        try:
            await _stream_session(
                orderbooks, start_time, last_commit_ref, last_stats_ref, msg_id_ref
            )
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
    log.info("Starting streamer — duration=%ds commit_interval=%ds stats_interval=%ds",
             STREAM_DURATION, COMMIT_INTERVAL, STATS_INTERVAL)
    asyncio.run(run())
