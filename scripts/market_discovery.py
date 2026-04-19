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


if __name__ == "__main__":
    active = get_active_tickers()
    if active:
        print("Active 15-min markets:")
        for t in active:
            print(f"  {t}")
    else:
        print("No active markets found.")
