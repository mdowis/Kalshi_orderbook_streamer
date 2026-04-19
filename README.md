# Kalshi Orderbook Streamer

Streams real-time orderbook data for Bitcoin and Ethereum 15-minute binary option contracts from [Kalshi](https://kalshi.com) and commits it as JSONL files to this repository.

## How it works

A GitHub Actions workflow runs four times per day (every 6 hours). Each job:

1. Authenticates with the Kalshi API using RSA-PSS signing
2. Discovers all currently open `KXBTC15M` and `KXETH15M` markets
3. Connects to the Kalshi WebSocket and subscribes to live orderbook events
4. Persists every snapshot and delta as a line in a `.jsonl` file under `data/`
5. Commits accumulated data back to this repo roughly every minute and on each market settlement

Four non-overlapping 6-hour jobs (00:00, 06:00, 12:00, 18:00 UTC) provide continuous 24-hour coverage. Auto-reconnect with exponential backoff handles dropped connections.

## Data format

```
data/
  KXBTC15M/
    2026-04-19/
      KXBTC15M-26APR191500.jsonl
      KXBTC15M-26APR191515.jsonl
  KXETH15M/
    2026-04-19/
      KXETH15M-26APR191500.jsonl
```

One file per 15-minute market instance. Each line is a JSON event in chronological order. Six record types are stored:

**meta** — market metadata, written once at subscription time:
```json
{"type":"meta","ticker":"KXBTC15M-26APR191500",
 "floor_strike":94000,"cap_strike":95000,"strike_type":"greater_or_equal",
 "open_time":"2026-04-19T14:45:00Z","close_time":"2026-04-19T15:00:00Z",
 "yes_sub_title":"≥ $94,000","no_sub_title":"< $94,000","status":"open"}
```

**snapshot** — full orderbook state, emitted on subscription and after reconnect:
```json
{"type":"snapshot","ts":1745078400.123,"ticker":"KXBTC15M-26APR191500","seq":1,
 "yes":[["0.5500","150.00"],["0.5400","320.00"]],
 "no": [["0.4600","200.00"],["0.4500","180.00"]]}
```

**delta** — incremental orderbook change, emitted on every price-level update:
```json
{"type":"delta","ts":1745078401.456,"ticker":"KXBTC15M-26APR191500","seq":2,
 "side":"yes","yes_dollars_fp":[["0.5500","200.00"]]}
```

**trade** — executed trade (matched order):
```json
{"type":"trade","ts":1745078402.789,"ticker":"KXBTC15M-26APR191500","seq":3,
 "side":"yes","price":"0.5500","count":10,"taker_side":"yes"}
```

**ticker** — real-time market statistics update:
```json
{"type":"ticker","ts":1745078403.001,"ticker":"KXBTC15M-26APR191500",
 "yes_bid":"0.5400","yes_ask":"0.5600","last_price":"0.5500",
 "volume":1250,"open_interest":340}
```

**stats** — REST-polled market stats snapshot (every 5 minutes):
```json
{"type":"stats","ts":1745078700.000,"ticker":"KXBTC15M-26APR191500",
 "last_price":"0.5500","volume":1340,"volume_24h":28400,
 "dollar_volume":737.0,"open_interest":342,"yes_bid":"0.54","yes_ask":"0.56"}
```

| Field | Description |
|-------|-------------|
| `ts` | Unix timestamp (UTC) |
| `seq` | Sequence number — gaps mean a missed event; the streamer re-subscribes to get a fresh snapshot on reconnect |
| `yes` / `no` | Price levels sorted best-bid first: `[[price, size], ...]` |
| `yes_dollars_fp` / `no_dollars_fp` | Partial level updates in a delta (only changed levels) |
| `price` | Dollar price per contract (e.g. `"0.5500"` = $0.55) |
| `size` | Dollar value of resting liquidity (e.g. `"150.00"` = $150) |
| `side` | `"yes"` or `"no"` |
| `count` | Number of contracts in a trade |
| `floor_strike` / `cap_strike` | BTC/ETH price range that resolves YES |
| `volume` / `volume_24h` | Contracts traded (total / last 24 h) |
| `open_interest` | Contracts currently outstanding |

### Liquidity

All liquidity data is captured. Each price level's size value is the dollar amount available to trade at that price. From any reconstructed state you can derive:

- **Depth at any price** — the size at each level
- **Total liquidity** — sum of all size values across all levels
- **Best bid/ask spread** — `1.0 - best_yes_price - best_no_price`
- **Market depth within a range** — sum sizes between two price thresholds

### Reconstructing the full orderbook

The JSONL files store snapshots + deltas rather than a redundant full-state copy on every tick (~30–100× more compact). Use `reconstruct.py` to get the complete orderbook at any moment:

```bash
# Latest state
python scripts/reconstruct.py KXBTC15M-26APR191500

# State as of a specific time
python scripts/reconstruct.py KXBTC15M-26APR191500 --at 2026-04-19T01:10:00

# Show all price levels
python scripts/reconstruct.py KXBTC15M-26APR191500 --levels 200

# JSON output for use in other scripts
python scripts/reconstruct.py KXBTC15M-26APR191500 --json

# List all tickers with saved data
python scripts/reconstruct.py --list
```

Or use it as a library:

```python
from scripts.reconstruct import reconstruct

state = reconstruct("KXBTC15M-26APR191500")

total_yes_liq = sum(float(s) for _, s in state["yes"])
total_no_liq  = sum(float(s) for _, s in state["no"])
best_yes      = float(state["yes"][0][0]) if state["yes"] else 0
best_no       = float(state["no"][0][0])  if state["no"]  else 0
spread        = 1.0 - best_yes - best_no

print(f"YES liquidity: ${total_yes_liq:,.2f}")
print(f"NO liquidity:  ${total_no_liq:,.2f}")
print(f"Spread:        ${spread:.4f}")
```

## Setup

### 1. Add GitHub Repository Secrets

Navigate to your repository on GitHub, then go to **Settings → Secrets and variables → Actions → Repository secrets** and click **New repository secret** for each of the following:

| Secret name | Value |
|-------------|-------|
| `KALSHI_API_KEY_ID` | The API Key ID string from Kalshi → Account & Security → API Keys |
| `KALSHI_PRIVATE_KEY` | The full contents of your downloaded `.pem` private key file |

For `KALSHI_PRIVATE_KEY`, paste the entire PEM block including the header and footer lines:
```
-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
```
GitHub supports multi-line secret values — paste as-is.

> **Repository secrets vs Environment secrets:** use Repository secrets here. Environment secrets are scoped to specific deployment environments (e.g. `production`) and require additional configuration that this workflow does not use.

### 2. Activate the workflow

Merge this branch into your default branch. The scheduled trigger will activate automatically. To start collecting immediately, go to **Actions → Stream Kalshi Orderbook → Run workflow**.

## Local development

```bash
pip install -r requirements.txt

export KALSHI_API_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY="$(cat /path/to/your/private_key.pem)"

# Smoke-test auth
python scripts/kalshi_auth.py

# Verify connection and check active markets
python scripts/diagnose.py

# Run a short 60-second stream (writes to data/ locally, skips git commit)
STREAM_DURATION_SECONDS=60 python scripts/stream_orderbook.py

# Reconstruct the orderbook from saved data
python scripts/reconstruct.py --list
```

Copy `.env.example` to `.env` and fill in your values if you prefer loading from a file. The streamer skips git commits when `GITHUB_ACTIONS` is not set, so local runs are safe.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/kalshi_auth.py` | Generates RSA-PSS signed headers for Kalshi requests |
| `scripts/market_discovery.py` | Fetches open KXBTC15M / KXETH15M tickers, market metadata, and REST stats |
| `scripts/orderbook_state.py` | In-memory orderbook; applies snapshots and deltas |
| `scripts/github_storage.py` | Appends JSONL records and git-commits from within Actions |
| `scripts/stream_orderbook.py` | Main entry point — streams orderbook, trades, ticker updates, and stats |
| `scripts/diagnose.py` | Checks auth, REST connectivity, and WebSocket before streaming |
| `scripts/reconstruct.py` | Replays JSONL data to return the full orderbook at any timestamp |

## Security

- API credentials are stored exclusively in GitHub Secrets and injected as environment variables at runtime
- No secrets are logged, printed in full, or committed to the repository
- `.gitignore` excludes `.env`, `.pem`, and `.key` files
- Data files contain only public market orderbook data — no user or account information
