"""Reconstruct the full orderbook state at any point in time from saved JSONL data.

Replays the initial snapshot plus all subsequent deltas up to a given timestamp.
Can also be imported and used as a library.

Usage:
  # Latest state for a ticker
  python scripts/reconstruct.py KXBTC15M-26APR182115-15

  # State as of a specific moment
  python scripts/reconstruct.py KXBTC15M-26APR182115-15 --at 2026-04-19T01:10:00

  # Machine-readable JSON output
  python scripts/reconstruct.py KXBTC15M-26APR182115-15 --json

  # List all tickers with saved data
  python scripts/reconstruct.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR  = REPO_ROOT / "data"


# ── File discovery ────────────────────────────────────────────────────────────

def find_file(ticker: str) -> Path | None:
    series = ticker.split("-")[0]
    series_dir = DATA_DIR / series
    if not series_dir.exists():
        return None
    matches = sorted(series_dir.rglob(f"{ticker}.jsonl"))
    return matches[0] if matches else None


def list_tickers() -> list[str]:
    return sorted(p.stem for p in DATA_DIR.rglob("*.jsonl"))


# ── Core reconstruction ───────────────────────────────────────────────────────

def reconstruct(ticker: str, at_ts: float | None = None) -> dict | None:
    """Replay JSONL records and return the orderbook state.

    Args:
        ticker:  Market ticker string, e.g. "KXBTC15M-26APR182115-15".
        at_ts:   Unix timestamp ceiling — only records with ts ≤ at_ts are
                 applied.  None means use every record in the file.

    Returns:
        dict with keys ticker, seq, ts, yes, no  — or None if no data found.
        yes / no are lists of [price_str, size_str] sorted best-bid first.
    """
    path = find_file(ticker)
    if path is None:
        return None

    yes: dict[str, str] = {}
    no:  dict[str, str] = {}
    seq    = 0
    last_ts = 0.0
    found  = False

    with path.open() as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ts = float(record.get("ts", 0))
            if at_ts is not None and ts > at_ts:
                break

            rtype = record.get("type", "")

            if rtype == "snapshot":
                seq    = record.get("seq", 0)
                last_ts = ts
                yes    = {p: s for p, s in record.get("yes", [])}
                no     = {p: s for p, s in record.get("no",  [])}
                found  = True

            elif rtype == "delta":
                seq    = record.get("seq", seq + 1)
                last_ts = ts
                side   = record.get("side", "")
                price  = record.get("price", "")
                delta  = float(record.get("delta", 0))
                levels = yes if side == "yes" else no
                new    = float(levels.get(price, "0")) + delta
                if new <= 0:
                    levels.pop(price, None)
                else:
                    levels[price] = f"{new:.2f}"
                found = True

    if not found:
        return None

    return {
        "ticker": ticker,
        "seq":    seq,
        "ts":     last_ts,
        "yes":    sorted(yes.items(), key=lambda x: float(x[0]), reverse=True),
        "no":     sorted(no.items(),  key=lambda x: float(x[0]), reverse=True),
    }


# ── Display ───────────────────────────────────────────────────────────────────

def print_orderbook(state: dict, max_levels: int = 20) -> None:
    ts_str = datetime.fromtimestamp(state["ts"], tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    yes = state["yes"]
    no  = state["no"]

    print(f"\nOrderbook: {state['ticker']}")
    print(f"As of:     {ts_str}  (seq={state['seq']})")
    print(f"Levels:    {len(yes)} YES  |  {len(no)} NO\n")

    header = f"{'Price':>10}  {'YES size ($)':>14}    {'Price':>10}  {'NO size ($)'}"
    print(header)
    print("-" * len(header))

    rows = min(max(len(yes), len(no)), max_levels)
    for i in range(rows):
        y = f"{yes[i][0]:>10}  {yes[i][1]:>14}" if i < len(yes) else " " * 26
        n = f"{no[i][0]:>10}  {no[i][1]:>14}"   if i < len(no)  else ""
        print(f"{y}    {n}")

    hidden = max(len(yes), len(no)) - max_levels
    if hidden > 0:
        print(f"  … {hidden} more level(s) hidden (use --levels N to show more)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconstruct a Kalshi orderbook from saved JSONL data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ticker", nargs="?",
                        help="Market ticker, e.g. KXBTC15M-26APR182115-15")
    parser.add_argument("--at",
                        help="ISO timestamp ceiling, e.g. 2026-04-19T01:10:00 (UTC assumed)")
    parser.add_argument("--levels", type=int, default=20,
                        help="Max price levels to display per side (default 20)")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Output raw JSON instead of a formatted table")
    parser.add_argument("--list", action="store_true",
                        help="List all tickers with saved data and exit")

    args = parser.parse_args()

    if args.list:
        tickers = list_tickers()
        if tickers:
            print("Available tickers:")
            for t in tickers:
                print(f"  {t}")
        else:
            print("No data files found under data/")
        sys.exit(0)

    if not args.ticker:
        parser.print_help()
        sys.exit(1)

    at_ts: float | None = None
    if args.at:
        dt = datetime.fromisoformat(args.at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        at_ts = dt.timestamp()

    state = reconstruct(args.ticker, at_ts)

    if state is None:
        print(f"No data found for ticker: {args.ticker}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(state, indent=2))
    else:
        print_orderbook(state, max_levels=args.levels)


if __name__ == "__main__":
    main()
