import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv("/Users/ningyunpeng/ATCcowork/backend/.env")

JIRA_BASE = os.getenv("JIRA_BASE_URL")
TOKEN = os.getenv("JIRA_API_TOKEN")

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

async def main():
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as c:
        # Get transition screen fields for transition 11
        resp = await c.get(f"{JIRA_BASE}/rest/api/2/issue/ACMS-57/transitions?expand=transitions.fields&transitionId=11", headers=headers)
        print("=== Transition 11 fields ===")
        import json
        data = resp.json()
        transitions = data.get("transitions", [])
        for t in transitions:
            if t["id"] == "11":
                fields = t.get("fields", {})
                for fname, fdata in fields.items():
                    print(f"\n{fname}: {fdata.get('name', '')} (required={fdata.get('required', False)})")
                    allowed = fdata.get("allowedValues", [])
                    if allowed:
                        print(f"  options: {[v.get('value', v.get('name', v)) for v in allowed[:10]]}")
                    schema = fdata.get("schema", {})
                    print(f"  type: {schema.get('type', 'unknown')}, system: {schema.get('system', '')}")
        
        # Also check what transitions 211 and 111 need
        resp2 = await c.get(f"{JIRA_BASE}/rest/api/2/issue/ACMS-57/transitions?expand=transitions.fields", headers=headers)
        print("\n=== All available transitions ===")
        all_t = resp2.json().get("transitions", [])
        for t in all_t:
            print(f"ID {t['id']}: {t['name']} -> {t['to']['name']}")

asyncio.run(main())
