"""Discover currently open BTC and ETH 15-minute contract tickers via Kalshi REST API."""

import os
import sys

import requests

# Insert scripts/ on path so this file can be run standalone.
sys.path.insert(0, os.path.dirname(__file__))
from kalshi_auth import get_auth_headers

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXBTC15M", "KXETH15M"]
REST_PATH = "/trade-api/v2/markets"


def get_active_tickers() -> list[str]:
    """Return ticker strings for all open BTC and ETH 15-min markets."""
    tickers: list[str] = []
    for series in SERIES:
        headers = get_auth_headers("GET", REST_PATH)
        resp = requests.get(
            f"{BASE_URL}/markets",
            params={"series_ticker": series, "status": "open"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
        tickers.extend(m["ticker"] for m in markets)
    return tickers


def get_market_metadata(ticker: str) -> dict:
    """Fetch full market metadata for a single ticker.

    Returns relevant fields: strike, expiry, subtitle, floor/cap strike, etc.
    Returns an empty dict on error rather than raising.
    """
    path = f"/trade-api/v2/markets/{ticker}"
    try:
        headers = get_auth_headers("GET", path)
        resp = requests.get(
            f"{BASE_URL}/markets/{ticker}",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        m = resp.json().get("market", {})
        return {
            "type":             "meta",
            "ticker":           ticker,
            "series_ticker":    m.get("series_ticker"),
            "title":            m.get("title"),
            "yes_sub_title":    m.get("yes_sub_title"),
            "no_sub_title":     m.get("no_sub_title"),
            "floor_strike":     m.get("floor_strike"),
            "cap_strike":       m.get("cap_strike"),
            "strike_type":      m.get("strike_type"),
            "open_time":        m.get("open_time"),
            "close_time":       m.get("close_time"),
            "expiration_time":  m.get("expiration_time"),
            "market_type":      m.get("market_type"),
            "result":           m.get("result"),
            "status":           m.get("status"),
        }
    except Exception as exc:
        return {"type": "meta", "ticker": ticker, "error": str(exc)}


def get_market_stats(ticker: str) -> dict | None:
    """Fetch current market statistics (volume, open interest, last price).

    Returns a stats record or None on error.
    """
    import time
    path = f"/trade-api/v2/markets/{ticker}"
    try:
        headers = get_auth_headers("GET", path)
        resp = requests.get(
            f"{BASE_URL}/markets/{ticker}",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        m = resp.json().get("market", {})
        return {
            "type":              "stats",
            "ts":                time.time(),
            "ticker":            ticker,
            "last_price":        m.get("last_price"),
            "previous_price":    m.get("previous_price"),
            "volume":            m.get("volume"),
            "volume_24h":        m.get("volume_24h"),
            "dollar_volume":     m.get("dollar_volume"),
            "dollar_volume_24h": m.get("dollar_volume_24h"),
            "open_interest":     m.get("open_interest"),
            "liquidity":         m.get("liquidity"),
            "yes_bid":           m.get("yes_bid"),
            "yes_ask":           m.get("yes_ask"),
            "no_bid":            m.get("no_bid"),
            "no_ask":            m.get("no_ask"),
        }
    except Exception:
        return None


if __name__ == "__main__":
    active = get_active_tickers()
    if active:
        print("Active 15-min markets:")
        for t in active:
            print(f"  {t}")
            meta = get_market_metadata(t)
            print(f"    floor_strike={meta.get('floor_strike')}  "
                  f"cap_strike={meta.get('cap_strike')}  "
                  f"close_time={meta.get('close_time')}")
    else:
        print("No active markets found.")
