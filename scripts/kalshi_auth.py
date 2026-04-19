"""RSA-PSS request signing for the Kalshi API."""

import base64
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _load_private_key():
    pem = os.environ["KALSHI_PRIVATE_KEY"]
    # GitHub Actions may escape newlines as literal \n in some secret formats.
    pem = pem.replace("\\n", "\n")
    return serialization.load_pem_private_key(pem.encode(), password=None)


def get_auth_headers(method: str, path: str) -> dict[str, str]:
    """Return the three Kalshi auth headers for a given HTTP method and path.

    path should be the raw path without query parameters,
    e.g. "/trade-api/v2/markets" or "/trade-api/ws/v2".
    """
    api_key_id = os.environ["KALSHI_API_KEY_ID"]
    private_key = _load_private_key()

    timestamp_ms = int(time.time() * 1000)
    # Strip query string — Kalshi signs the path only.
    path_no_query = path.split("?")[0]
    message = f"{timestamp_ms}{method.upper()}{path_no_query}".encode()

    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
    }


if __name__ == "__main__":
    # Smoke test: print header keys without exposing values.
    headers = get_auth_headers("GET", "/trade-api/ws/v2")
    for k, v in headers.items():
        # Show only the first 8 chars of each value for sanity checking.
        print(f"{k}: {v[:8]}...")
    print("Auth headers generated successfully.")
