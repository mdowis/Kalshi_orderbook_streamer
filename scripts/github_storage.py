"""Write JSONL data files locally and commit them to the GitHub repo.

When running inside GitHub Actions the runner already has git configured with
write access via GITHUB_TOKEN.  When running locally this module writes files
but skips commits (CI=false / GITHUB_ACTIONS not set).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# Root of the git working tree — the directory that contains data/.
REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"


def _data_path(ticker: str, ts: float) -> Path:
    """Return (and create) the JSONL file path for a given ticker and timestamp."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    series = ticker.split("-")[0]  # e.g. "KXBTC15M"
    dir_path = DATA_DIR / series / dt.strftime("%Y-%m-%d")
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / f"{ticker}.jsonl"


def append_record(ticker: str, record: dict, ts: float | None = None) -> None:
    """Append one JSON record to the ticker's JSONL file."""
    ts = ts if ts is not None else time.time()
    path = _data_path(ticker, ts)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)


def commit_data(message: str) -> bool:
    """Stage data/ and commit+push if running inside GitHub Actions.

    Returns True if a commit was made, False otherwise.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[storage] Not in GitHub Actions — skipping git commit.")
        return False

    _run(["git", "add", str(DATA_DIR)])

    diff = _run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("[storage] Nothing to commit.")
        return False

    result = _run(["git", "commit", "-m", message])
    if result.returncode != 0:
        print(f"[storage] Commit failed: {result.stderr.strip()}")
        return False

    # Push with up to 3 retries; pull --rebase on conflict.
    for attempt in range(1, 4):
        push = _run(["git", "push"])
        if push.returncode == 0:
            print(f"[storage] Pushed: {message}")
            return True
        print(f"[storage] Push attempt {attempt} failed, rebasing…")
        _run(["git", "pull", "--rebase"])
        time.sleep(2 ** attempt)

    print("[storage] Push failed after retries.")
    return False
