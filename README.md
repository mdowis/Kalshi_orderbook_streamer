# Kalshi Orderbook Streamer

Streams real-time orderbook data for Bitcoin and Ethereum 15-minute binary option contracts from [Kalshi](https://kalshi.com) and commits it as JSONL files to this repository.

## How it works

A GitHub Actions workflow runs four times per day (every 6 hours). Each job:

1. Authenticates with the Kalshi API using RSA-PSS signing
2. Discovers all currently open `KXBTC15M` and `KXETH15M` markets
3. Connects to the Kalshi WebSocket and subscribes to `orderbook_delta` events
4. Applies every snapshot and delta to maintain a live orderbook in memory
5. Persists each event as a line in a `.jsonl` file under `data/`
6. Commits accumulated data back to this repo on each market close (~every 15 min) and at job end

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

One file per 15-minute market instance. Each line is a JSON event:

**Snapshot** (emitted on subscription and after reconnect):
```json
{"type":"snapshot","ts":1745078400.123,"ticker":"KXBTC15M-26APR191500","seq":1,"yes":[[65,10],[64,25]],"no":[[36,8],[35,20]]}
```

**Delta** (emitted on every orderbook change):
```json
{"type":"delta","ts":1745078401.456,"ticker":"KXBTC15M-26APR191500","seq":2,"side":"yes","price":65,"delta":-5}
```

| Field | Description |
|-------|-------------|
| `ts` | Unix timestamp (UTC) |
| `seq` | Kalshi sequence number — gaps indicate a missed event; re-snapshot on reconnect |
| `yes` / `no` | Price levels sorted best-bid first: `[[price_cents, contracts], ...]` |
| `side` | `"yes"` or `"no"` |
| `price` | Price level in cents (0–99) |
| `delta` | Change in contract quantity (positive = more, negative = fewer) |

To reconstruct the full orderbook at any point, start from the most recent snapshot and apply all subsequent deltas in sequence order.

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

# List currently open markets
python scripts/market_discovery.py

# Run a short 60-second stream (writes to data/ locally, skips git commit)
STREAM_DURATION_SECONDS=60 python scripts/stream_orderbook.py
```

Copy `.env.example` to `.env` and fill in your values if you prefer loading from a file. The streamer skips git commits when `GITHUB_ACTIONS` is not set, so local runs are safe.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/kalshi_auth.py` | Generates RSA-PSS signed headers for Kalshi requests |
| `scripts/market_discovery.py` | Fetches open KXBTC15M / KXETH15M tickers from the REST API |
| `scripts/orderbook_state.py` | In-memory orderbook; applies snapshots and deltas |
| `scripts/github_storage.py` | Appends JSONL records and git-commits from within Actions |
| `scripts/stream_orderbook.py` | Main entry point — async WebSocket loop with auto-reconnect |

## Security

- API credentials are stored exclusively in GitHub Secrets and injected as environment variables at runtime
- No secrets are logged, printed in full, or committed to the repository
- `.gitignore` excludes `.env`, `.pem`, and `.key` files
- Data files contain only public market orderbook data — no user or account information
