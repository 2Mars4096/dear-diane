#!/usr/bin/env python3
"""Debug script to diagnose dan-chat connection issues. Run: python scripts/debug_dan_chat.py"""

import asyncio
import os
import sys

# Ensure project is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    base_url = os.environ.get("DAN_SERVER_URL") or "http://127.0.0.1:8000"
    print(f"1. DAN_SERVER_URL: {os.environ.get('DAN_SERVER_URL', '(not set)')}")
    print(f"2. Base URL: {base_url}")

    # Raw httpx test
    import httpx
    print(f"\n3. Testing httpx GET {base_url}/health ...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/health")
            print(f"   Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"   Body: {resp.json()}")
    except Exception as e:
        print(f"   FAILED: {type(e).__name__}: {e}")

    # ChatClient ping
    print(f"\n4. Testing ChatClient.ping() ...")
    from dan.cli.chat import ChatClient
    client = ChatClient(base_url=base_url)
    ok, err = await client.ping()
    await client.close()
    print(f"   ok={ok}, err={err!r}")

    if ok:
        print("\n✓ Connection OK. dan-chat should work. If it doesn't, try: python -m dan.cli.chat")
    else:
        print("\n✗ Connection failed. Check that dan-serve is running and reachable.")
        print("  Try: curl http://127.0.0.1:8000/health")
        print("  Or:  dan-chat --server http://127.0.0.1:8000")

if __name__ == "__main__":
    asyncio.run(main())
