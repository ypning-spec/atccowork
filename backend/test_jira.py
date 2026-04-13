"""
Standalone test script for JIRA transitions on ACMS project.
Reads config from .env, then:
  1. Lists available transitions for ACMS-57
  2. Attempts to execute transition ID 11 (分析完成 → 问题解决中) on ACMS-57
"""

import asyncio
import os
import sys

# ---------------------------------------------------------------------------
# Bootstrap: make sure the app package is importable even when running the
# script directly from the repo root (without installing the package).
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Load .env manually before importing app modules so that lru_cache'd
# get_settings() picks up the real values.
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import httpx
from app.config import get_settings
from app.services.jira import get_transitions, transition_issue, get_issue, search_issues

settings = get_settings()

ISSUE_KEY = "ACMS-57"
TARGET_TRANSITION_ID = "11"   # 分析完成 → 问题解决中


# ---------------------------------------------------------------------------
# Helper: raw HTTP transition call so we can see the exact response body
# ---------------------------------------------------------------------------
async def raw_transition(issue_key: str, transition_id: str) -> tuple[int, str]:
    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {settings.jira_api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        follow_redirects=True,
        timeout=15,
    ) as c:
        url = f"{settings.jira_base_url}/rest/api/2/issue/{issue_key}/transitions"
        resp = await c.post(url, json={"transition": {"id": transition_id}})
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Main test routine
# ---------------------------------------------------------------------------
async def main() -> None:
    sep = "-" * 60

    print(sep)
    print("JIRA Transition Test")
    print(f"  JIRA base URL : {settings.jira_base_url}")
    print(f"  Project key   : {settings.jira_project_key}")
    print(f"  Target issue  : {ISSUE_KEY}")
    print(sep)

    # ------------------------------------------------------------------
    # Step 0: Confirm the issue exists and show its current status
    # ------------------------------------------------------------------
    print("\n[0] Fetching issue details ...")
    try:
        issue_data = await get_issue(ISSUE_KEY)
        fields = issue_data.get("fields", {})
        summary = fields.get("summary", "(no summary)")
        status  = fields.get("status", {}).get("name", "unknown")
        print(f"    Key     : {issue_data.get('key')}")
        print(f"    Summary : {summary}")
        print(f"    Status  : {status}")
    except httpx.HTTPStatusError as exc:
        print(f"    ERROR fetching issue: HTTP {exc.response.status_code} — {exc.response.text}")
        return
    except Exception as exc:
        print(f"    ERROR: {exc}")
        return

    # ------------------------------------------------------------------
    # Step 1: List available transitions
    # ------------------------------------------------------------------
    print(f"\n[1] Available transitions for {ISSUE_KEY} ...")
    try:
        transitions = await get_transitions(ISSUE_KEY)
        if not transitions:
            print("    (no transitions returned — issue may already be closed, or token lacks permission)")
        else:
            print(f"    {'ID':<8} {'Name':<30} {'To Status'}")
            print(f"    {'--':<8} {'----':<30} {'---------'}")
            for t in transitions:
                tid  = t.get("id", "?")
                name = t.get("name", "?")
                to   = t.get("to", {}).get("name", "?")
                print(f"    {tid:<8} {name:<30} {to}")
    except httpx.HTTPStatusError as exc:
        print(f"    ERROR: HTTP {exc.response.status_code} — {exc.response.text}")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    # ------------------------------------------------------------------
    # Step 2: Execute transition ID 11 (分析完成 → 问题解决中)
    # ------------------------------------------------------------------
    print(f"\n[2] Attempting transition ID {TARGET_TRANSITION_ID} on {ISSUE_KEY} ...")
    status_code, body = await raw_transition(ISSUE_KEY, TARGET_TRANSITION_ID)
    print(f"    HTTP status : {status_code}")
    if body.strip():
        print(f"    Response    : {body[:500]}")
    else:
        print(f"    Response    : (empty body — transition accepted)")

    if status_code in (200, 204):
        print("    RESULT      : SUCCESS — transition was accepted by JIRA")
    else:
        print("    RESULT      : FAILED  — transition was rejected")

    # ------------------------------------------------------------------
    # Step 3: Confirm new status after transition
    # ------------------------------------------------------------------
    if status_code in (200, 204):
        print(f"\n[3] Re-fetching {ISSUE_KEY} to confirm new status ...")
        try:
            issue_data2 = await get_issue(ISSUE_KEY)
            new_status = issue_data2.get("fields", {}).get("status", {}).get("name", "unknown")
            print(f"    Status after transition : {new_status}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

    print(f"\n{sep}")
    print("Test complete.")
    print(sep)


if __name__ == "__main__":
    asyncio.run(main())
