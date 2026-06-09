"""
dremio_client.py
────────────────
Handles all communication with the Dremio REST API.

Responsibilities:
  1. Authentication:
       - "bearer" mode  → sends `Authorization: Bearer <DREMIO_API_KEY>` header.
       - "legacy" mode  → authenticates via POST /apiv2/login (username + password)
                          and uses the returned `_dremio<token>` format.
  2. SQL submission → POST /api/v3/sql
  3. Job polling    → GET  /api/v3/job/{jobId}  (until COMPLETED or FAILED)
  4. Result fetch   → GET  /api/v3/job/{jobId}/results
  5. Returns a plain dict with keys matching the SQL column aliases
     (total_lignes, valides, score_completude_pct).

Environment variables consumed (loaded from .env):
  DREMIO_HOST          Full base URL, e.g. http://dlakegtwprd:9047
  DREMIO_AUTH_TYPE     "bearer" (default) or "legacy"
  DREMIO_API_KEY       PAT token — used when DREMIO_AUTH_TYPE=bearer
  DREMIO_USERNAME      Used when DREMIO_AUTH_TYPE=legacy
  DREMIO_PASSWORD      Used when DREMIO_AUTH_TYPE=legacy
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DREMIO_HOST: str = os.getenv("DREMIO_HOST", "http://dlakegtwprd:9047").rstrip("/")
DREMIO_AUTH_TYPE: str = os.getenv("DREMIO_AUTH_TYPE", "bearer").lower()
DREMIO_API_KEY: str = os.getenv("DREMIO_API_KEY", "")
DREMIO_USERNAME: str = os.getenv("DREMIO_USERNAME", "")
DREMIO_PASSWORD: str = os.getenv("DREMIO_PASSWORD", "")

# Polling behaviour
POLL_INTERVAL_SEC: float = 2.0      # seconds between job status checks
JOB_TIMEOUT_SEC: float = 300.0     # max seconds to wait for a single query

# Terminal job states
_TERMINAL_STATES = {"COMPLETED", "CANCELED", "FAILED"}


# ── Authentication ────────────────────────────────────────────────────────────

class DremioAuthError(Exception):
    """Raised when authentication with Dremio fails."""


def _get_auth_header() -> Dict[str, str]:
    """
    Build and return the `Authorization` header dict for the configured auth mode.

    For "bearer" mode the PAT is used directly.
    For "legacy" mode a login call is made and the returned token is used.

    Returns:
        Dict with a single "Authorization" key.

    Raises:
        DremioAuthError: If credentials are missing or the login call fails.
    """
    if DREMIO_AUTH_TYPE == "bearer":
        if not DREMIO_API_KEY:
            raise DremioAuthError("DREMIO_API_KEY is not set in .env")
        return {"Authorization": f"Bearer {DREMIO_API_KEY}"}

    if DREMIO_AUTH_TYPE == "legacy":
        if not DREMIO_USERNAME or not DREMIO_PASSWORD:
            raise DremioAuthError(
                "DREMIO_USERNAME and DREMIO_PASSWORD must be set for DREMIO_AUTH_TYPE=legacy"
            )
        token = _legacy_login(DREMIO_USERNAME, DREMIO_PASSWORD)
        return {"Authorization": f"_dremio{token}"}

    raise DremioAuthError(
        f"Unknown DREMIO_AUTH_TYPE='{DREMIO_AUTH_TYPE}'. Use 'bearer' or 'legacy'."
    )


def _legacy_login(username: str, password: str) -> str:
    """
    Authenticate against Dremio's /apiv2/login endpoint.

    Args:
        username: Dremio username.
        password: Dremio password.

    Returns:
        Token string to be prefixed with `_dremio` in the Authorization header.

    Raises:
        DremioAuthError: If the login request fails.
    """
    url = f"{DREMIO_HOST}/apiv2/login"
    payload = {"userName": username, "password": password}

    try:
        resp = requests.post(url, json=payload, timeout=15)
    except requests.RequestException as exc:
        raise DremioAuthError(f"Login request failed: {exc}") from exc

    if resp.status_code != 200:
        raise DremioAuthError(
            f"Login failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    token = resp.json().get("token")
    if not token:
        raise DremioAuthError("Login response did not contain a token.")

    logger.debug("Legacy login successful for user '%s'", username)
    return token


# ── Dremio REST helpers ───────────────────────────────────────────────────────

class DremioQueryError(Exception):
    """Raised when a Dremio SQL job fails or times out."""


def _submit_sql(sql: str, headers: Dict[str, str]) -> str:
    """
    Submit a SQL query to Dremio and return the job ID.

    Args:
        sql:     SQL string to execute.
        headers: HTTP headers including Authorization.

    Returns:
        Dremio job ID string.

    Raises:
        DremioQueryError: If the submission fails.
    """
    url = f"{DREMIO_HOST}/api/v3/sql"
    payload = {"sql": sql}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise DremioQueryError(f"SQL submission request failed: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise DremioQueryError(
            f"SQL submission failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    job_id = resp.json().get("id")
    if not job_id:
        raise DremioQueryError("Dremio response did not include a job ID.")

    logger.debug("Submitted SQL → jobId: %s", job_id)
    return job_id


def _poll_job(job_id: str, headers: Dict[str, str]) -> str:
    """
    Poll a Dremio job until it reaches a terminal state.

    Args:
        job_id:  Dremio job identifier.
        headers: HTTP headers including Authorization.

    Returns:
        Final job state string (e.g. "COMPLETED").

    Raises:
        DremioQueryError: If the job fails, is cancelled, or times out.
    """
    url = f"{DREMIO_HOST}/api/v3/job/{job_id}"
    deadline = time.monotonic() + JOB_TIMEOUT_SEC

    while True:
        if time.monotonic() > deadline:
            raise DremioQueryError(
                f"Job {job_id} timed out after {JOB_TIMEOUT_SEC}s"
            )

        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise DremioQueryError(f"Job poll request failed: {exc}") from exc

        if resp.status_code != 200:
            raise DremioQueryError(
                f"Job poll failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        state: str = resp.json().get("jobState", "")
        logger.debug("Job %s state: %s", job_id, state)

        if state in _TERMINAL_STATES:
            if state != "COMPLETED":
                error_msg = resp.json().get("errorMessage", "no detail")
                raise DremioQueryError(
                    f"Job {job_id} ended with state '{state}': {error_msg}"
                )
            return state

        time.sleep(POLL_INTERVAL_SEC)


def _fetch_results(job_id: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Retrieve the result rows of a completed Dremio job.

    Args:
        job_id:  Dremio job identifier.
        headers: HTTP headers including Authorization.

    Returns:
        List of row dicts, e.g. [{"total_lignes": 5000, "valides": 4800, ...}].

    Raises:
        DremioQueryError: If the results request fails.
    """
    url = f"{DREMIO_HOST}/api/v3/job/{job_id}/results"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise DremioQueryError(f"Results fetch request failed: {exc}") from exc

    if resp.status_code != 200:
        raise DremioQueryError(
            f"Results fetch failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    rows = data.get("rows", [])
    logger.debug("Job %s returned %d row(s).", job_id, len(rows))
    return rows


# ── Public interface ──────────────────────────────────────────────────────────

class DremioClient:
    """
    Stateless Dremio REST client.

    Usage:
        client = DremioClient()
        result = client.run_query("SELECT COUNT(*) ...")
        # result → {"total_lignes": 5000, "valides": 4800, "score_completude_pct": 96.0}
    """

    def __init__(self) -> None:
        # Resolve auth header once at construction time (one login call for legacy mode)
        self._headers: Dict[str, str] = {
            "Content-Type": "application/json",
            **_get_auth_header(),
        }
        logger.info(
            "DremioClient initialised  host=%s  auth_type=%s",
            DREMIO_HOST,
            DREMIO_AUTH_TYPE,
        )

    def run_query(self, sql: str) -> Optional[Dict[str, Any]]:
        """
        Execute a SQL query on Dremio and return the first result row as a dict.

        The DQ queries in checks_config.yaml each return exactly one row with
        columns: total_lignes, valides, score_completude_pct.

        Args:
            sql: SQL query string.

        Returns:
            Dict of column → value for the first result row, or None on error.
        """
        try:
            job_id = _submit_sql(sql, self._headers)
            _poll_job(job_id, self._headers)
            rows = _fetch_results(job_id, self._headers)
            if not rows:
                logger.warning("Query returned no rows. jobId=%s", job_id)
                return None
            return rows[0]  # DQ queries return a single aggregate row
        except DremioQueryError as exc:
            logger.error("Query execution failed: %s", exc)
            return None


# ── CLI entry point (for standalone connectivity test) ────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    client = DremioClient()
    test_sql = "SELECT 1 AS total_lignes, 1 AS valides, 100.0 AS score_completude_pct"
    result = client.run_query(test_sql)
    print("\nTest query result:", result)
