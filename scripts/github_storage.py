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


def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(REPO_ROOT),
        capture_output=capture, text=capture,
    )


def _check(cmd: list[str]) -> int:
    """Run a command and return its exit code; output flows to the Actions log."""
    return _run(cmd).returncode


def _has_staged_changes() -> bool:
    return _run(["git", "diff", "--cached", "--quiet"]).returncode != 0


def _current_branch() -> str:
    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    return r.stdout.strip()


def _recover_detached_head() -> None:
    """If a prior failed rebase left us in detached HEAD, get back on the branch."""
    if _current_branch() != "HEAD":
        return
    # Abort any in-progress rebase first.
    _check(["git", "rebase", "--abort"])
    branch = os.environ.get("GITHUB_REF_NAME", "")
    if not branch:
        # Fall back to reading the branch from remote tracking info.
        r = _run(["git", "branch", "-r", "--list", "origin/*"], capture=True)
        lines = [l.strip().removeprefix("origin/")
                 for l in r.stdout.splitlines()
                 if "HEAD" not in l]
        branch = lines[0] if lines else "main"
    print(f"[storage] Detached HEAD detected — checking out {branch}")
    _check(["git", "checkout", branch])


def _pull_with_union(branch: str) -> int:
    """Pull and rebase using the union strategy so JSONL add/add conflicts
    are resolved by keeping all lines from both sides."""
    return _check([
        "git", "pull", "--rebase", "-X", "union", "origin", branch,
    ])


def commit_data(message: str) -> bool:
    """Stage data/ and commit+push if running inside GitHub Actions.

    Returns True if a commit was made, False otherwise.
    All git output is forwarded to stdout so failures are visible in the Actions log.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[storage] Not in GitHub Actions — skipping git commit.")
        return False

    _recover_detached_head()

    _check(["git", "add", str(DATA_DIR)])

    if not _has_staged_changes():
        print("[storage] Nothing to commit.")
        return False

    if _check(["git", "commit", "-m", message]) != 0:
        print("[storage] Commit failed (see git output above).")
        return False

    branch = _current_branch()

    for attempt in range(1, 5):
        if _check(["git", "push"]) == 0:
            print(f"[storage] Pushed: {message}")
            return True
        print(f"[storage] Push attempt {attempt} failed — pulling with union strategy…")
        rc = _pull_with_union(branch)
        if rc != 0:
            # Union rebase still failed; abort cleanly before next attempt.
            _check(["git", "rebase", "--abort"])
        time.sleep(2 ** attempt)

    print("[storage] Push failed after retries (see git output above).")
    return False
