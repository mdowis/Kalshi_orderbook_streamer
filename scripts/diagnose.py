"""Run before the main streamer to verify every component works end-to-end.

Prints clear PASS/FAIL output for each stage so Actions log problems are obvious.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))


def section(title: str) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {title}")
    print("=" * 50)


# ── 1. Environment ────────────────────────────────────────────────────────────

def check_env() -> bool:
    section("1. Environment variables")
    key_id = os.environ.get("KALSHI_API_KEY_ID", "")
    pem    = os.environ.get("KALSHI_PRIVATE_KEY", "")

    print(f"KALSHI_API_KEY_ID  : {'SET  (' + key_id[:8] + '…)' if key_id else '*** MISSING ***'}")
    print(f"KALSHI_PRIVATE_KEY : {'SET  (' + str(len(pem)) + ' chars)' if pem else '*** MISSING ***'}")

    if not key_id or not pem:
        print("\nFAIL — add both secrets under Settings → Secrets → Actions → Repository secrets")
        return False

    # Check PEM looks right
    if "BEGIN" not in pem:
        print("WARN — KALSHI_PRIVATE_KEY does not look like a PEM block (no 'BEGIN' header)")
    print("\nPASS")
    return True


# ── 2. Auth header generation ─────────────────────────────────────────────────

def check_auth() -> bool:
    section("2. RSA-PSS auth header generation")
    try:
        from kalshi_auth import get_auth_headers
        headers = get_auth_headers("GET", "/trade-api/v2/markets")
        for k, v in headers.items():
            print(f"  {k}: {v[:16]}…")
        print("\nPASS")
        return True
    except Exception:
        traceback.print_exc()
        print("\nFAIL")
        return False


# ── 3. REST market discovery ──────────────────────────────────────────────────

def check_markets() -> list[str]:
    section("3. REST API — market discovery")
    import requests
    from kalshi_auth import get_auth_headers

    BASE_URL  = "https://api.elections.kalshi.com/trade-api/v2"
    REST_PATH = "/trade-api/v2/markets"
    tickers: list[str] = []

    for series in ["KXBTC15M", "KXETH15M"]:
        try:
            headers = get_auth_headers("GET", REST_PATH)
            resp = requests.get(
                f"{BASE_URL}/markets",
                params={"series_ticker": series, "status": "open"},
                headers=headers,
                timeout=10,
            )
            print(f"\n{series}  →  HTTP {resp.status_code}")
            if resp.status_code == 200:
                markets = resp.json().get("markets", [])
                print(f"  Open markets found: {len(markets)}")
                for m in markets[:5]:
                    t = m.get("ticker", "?")
                    print(f"    {t}")
                    tickers.append(t)
            else:
                print(f"  Response body: {resp.text[:300]}")
        except Exception:
            traceback.print_exc()

    print(f"\n{'PASS' if tickers else 'WARN — no open markets found (non-fatal if markets are between cycles)'}")
    return tickers


# ── 4. WebSocket connection ───────────────────────────────────────────────────

async def check_websocket(tickers: list[str]) -> bool:
    section("4. WebSocket — connect + subscribe + receive")
    if not tickers:
        print("Skipped — no tickers from step 3")
        return True  # not a failure in itself

    import websockets
    from kalshi_auth import get_auth_headers

    WS_URL  = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    WS_PATH = "/trade-api/ws/v2"
    ticker  = tickers[0]

    try:
        headers = get_auth_headers("GET", WS_PATH)
        print(f"Connecting to {WS_URL} …")
        async with websockets.connect(
            WS_URL,
            additional_headers=headers,
            open_timeout=15,
        ) as ws:
            print("Connected OK")
            sub = json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": [ticker],
                },
            })
            await ws.send(sub)
            print(f"Subscribed to {ticker}, waiting for up to 5 messages …\n")

            for i in range(5):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    print(f"  [{i+1}] Timeout — no message received in 15s")
                    break

                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"  [{i+1}] Non-JSON: {raw[:120]}")
                    continue

                msg_type = parsed.get("type", "<no type>")
                # Print full top-level structure so we can verify the format
                top_keys = list(parsed.keys())
                inner = parsed.get("msg", {})
                inner_keys = list(inner.keys()) if isinstance(inner, dict) else inner
                print(f"  [{i+1}] type={msg_type!r}  top-keys={top_keys}  msg-keys={inner_keys}")

        print("\nPASS")
        return True
    except Exception:
        traceback.print_exc()
        print("\nFAIL")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results: list[bool] = []

    results.append(check_env())
    if results[-1]:
        results.append(check_auth())
    if results[-1]:
        tickers = check_markets()
        results.append(asyncio.run(check_websocket(tickers)))

    section("Summary")
    if all(results):
        print("All checks PASSED — streamer should work correctly.")
        sys.exit(0)
    else:
        print("One or more checks FAILED — fix the issues above before streaming.")
        sys.exit(1)
