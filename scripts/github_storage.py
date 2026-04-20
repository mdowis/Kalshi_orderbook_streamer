"""Write JSONL data files locally and commit them to the GitHub repo.

When running inside GitHub Actions the runner already has git configured with
write access via GITHUB_TOKEN.  When running locally this module writes files
but skips commits (CI=false / GITHUB_ACTIONS not set).
"""

from __future__ import annotations

import os
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"

# Cache ticker → Path so a market that spans midnight always writes to the
# same file (determined by the timestamp of its first record, not wall-clock).
_path_cache: dict[str, Path] = {}


def _data_path(ticker: str, ts: float) -> Path:
    """Return (and create) the JSONL file path for a given ticker and timestamp."""
    if ticker in _path_cache:
        return _path_cache[ticker]

    series = ticker.split("-")[0]
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


# ── Git helpers ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=capture, text=capture)


def _check(cmd: list[str]) -> int:
    return _run(cmd).returncode


def _has_staged_changes() -> bool:
    return _run(["git", "diff", "--cached", "--quiet"]).returncode != 0


def _current_branch() -> str:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True).stdout.strip()


def _target_branch() -> str:
    """Branch this Actions job is running on."""
    return os.environ.get("GITHUB_REF_NAME", "") or _current_branch()


def _clean_git_state() -> None:
    """Abort any in-progress rebase/merge and get back on the branch."""
    _check(["git", "rebase", "--abort"])
    _check(["git", "merge", "--abort"])
    branch = _target_branch()
    if _current_branch() == "HEAD":
        _check(["git", "checkout", branch])


def _local_additions(path: Path, remote_ref: str) -> list[str]:
    """Lines in our local file that the remote doesn't have (our unsynced data)."""
    rel = str(path.relative_to(REPO_ROOT))
    r = _run(["git", "show", f"{remote_ref}:{rel}"], capture=True)
    remote_lines: set[str] = set(r.stdout.splitlines()) if r.returncode == 0 else set()
    try:
        return [
            line.rstrip("\n")
            for line in path.read_text().splitlines()
            if line.strip() and line.strip() not in remote_lines
        ]
    except FileNotFoundError:
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def commit_data(message: str) -> bool:
    """Sync local data files to remote and push.

    When R2_BUCKET is set, git data commits are skipped — R2 is the storage
    backend and the workflow uploads everything at job end.  This keeps the
    git repo from growing unboundedly.

    Strategy when git is used (no rebase, no conflicts):
      1. Fetch remote to get its latest state.
      2. Compute which lines each local JSONL file has that remote doesn't.
      3. Hard-reset working tree to remote (eliminates any diverged history).
      4. Re-append our unsynced lines on top of the remote files.
      5. Commit and push — we're exactly one commit ahead so push always succeeds.
      6. If push fails (another concurrent commit landed), retry the whole loop.

    Returns True if a commit was pushed, False otherwise.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[storage] Not in GitHub Actions — skipping git commit.")
        return False

    if os.environ.get("R2_BUCKET"):
        # R2 is the storage backend; the workflow syncs at job end.
        return False

    _clean_git_state()
    branch = _target_branch()

    for attempt in range(1, 5):
        remote_ref = f"origin/{branch}"
        _check(["git", "fetch", "origin", branch])

        # Collect every JSONL file we've been writing to, plus any others under data/.
        tracked = set(_path_cache.values())
        all_paths = tracked | set(DATA_DIR.rglob("*.jsonl"))

        additions: dict[Path, list[str]] = {}
        for path in all_paths:
            lines = _local_additions(path, remote_ref)
            if lines:
                additions[path] = lines

        if not additions:
            print("[storage] No local data to push.")
            return False

        # Reset to remote state — no merge conflicts possible after this.
        _check(["git", "reset", "--hard", remote_ref])

        # Re-apply our unsynced lines on top of the remote files.
        for path, lines in additions.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as fh:
                for line in lines:
                    fh.write(line + "\n")

        _check(["git", "add", str(DATA_DIR)])
        if not _has_staged_changes():
            print("[storage] Nothing new after sync.")
            return False

        _check(["git", "commit", "-m", message])

        if _check(["git", "push"]) == 0:
            print(f"[storage] Pushed ({len(additions)} file(s)): {message}")
            return True

        print(f"[storage] Push attempt {attempt} failed (concurrent commit) — retrying…")
        time.sleep(2 ** attempt)

    print("[storage] Push failed after retries.")
    return False
