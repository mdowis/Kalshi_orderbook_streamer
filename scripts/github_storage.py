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

# Cache ticker → Path so a market that spans midnight always writes to the
# same file (determined by the timestamp of its first record, not wall-clock).
_path_cache: dict[str, Path] = {}


def _data_path(ticker: str, ts: float) -> Path:
    """Return (and create) the JSONL file path for a given ticker and timestamp.

    If a file for this ticker already exists on disk (e.g. from a previous
    process run that started before midnight), reuse that path so the market's
    data stays in one file and git doesn't detect a rename.
    """
    if ticker in _path_cache:
        return _path_cache[ticker]

    series = ticker.split("-")[0]  # e.g. "KXBTC15M"

    # Prefer an existing file over creating a new dated one.
    existing = sorted((DATA_DIR / series).rglob(f"{ticker}.jsonl"))
    if existing:
        path = existing[0]
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        dir_path = DATA_DIR / series / dt.strftime("%Y-%m-%d")
        dir_path.mkdir(parents=True, exist_ok=True)
        path = dir_path / f"{ticker}.jsonl"

    _path_cache[ticker] = path
    return path


def append_record(ticker: str, record: dict, ts: float | None = None) -> None:
    """Append one JSON record to the ticker's JSONL file."""
    ts = ts if ts is not None else time.time()
    path = _data_path(ticker, ts)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _check(cmd: list[str]) -> int:
    """Run a command and return its exit code; output flows to the Actions log."""
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return result.returncode


def _has_staged_changes() -> bool:
    """Return True if there are staged changes ready to commit."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(REPO_ROOT),
    )
    return result.returncode != 0  # non-zero means there ARE changes


def commit_data(message: str) -> bool:
    """Stage data/ and commit+push if running inside GitHub Actions.

    Returns True if a commit was made, False otherwise.
    All git output is forwarded to stdout so failures are visible in the Actions log.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[storage] Not in GitHub Actions — skipping git commit.")
        return False

    _check(["git", "add", str(DATA_DIR)])

    if not _has_staged_changes():
        print("[storage] Nothing to commit.")
        return False

    if _check(["git", "commit", "-m", message]) != 0:
        print("[storage] Commit failed (see git output above).")
        return False

    # Push with up to 3 retries; pull --rebase on conflict.
    for attempt in range(1, 4):
        if _check(["git", "push"]) == 0:
            print(f"[storage] Pushed: {message}")
            return True
        print(f"[storage] Push attempt {attempt} failed, rebasing…")
        _check(["git", "pull", "--rebase"])
        time.sleep(2 ** attempt)

    print("[storage] Push failed after retries (see git output above).")
    return False
