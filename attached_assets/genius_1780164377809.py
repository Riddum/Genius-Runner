import asyncio
import aiohttp
import json
import time

BASE_URL = "https://www.indiageniuschallenge.com/api"

# Three different anon_attempt_id (one per request)
ANON_IDS = [
    "6a1b21b6e0861040e20e7e96",   # second ID
    "6a1b223ffe0013c544a3622a",   # third ID
    "6a1b163b72c73c8e938703be",   # first ID
]

# Cookie files for each request (same account, can be same file copied three times)
COOKIE_FILES = [
    "cookies_account1.json",
    "cookies_account2.json",
    "cookies_account3.json",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.indiageniuschallenge.com/login?redirect=%2Ffriends",
    "Origin": "https://www.indiageniuschallenge.com",
}

def load_cookies(file_path):
    with open(file_path, 'r') as f:
        cookie_list = json.load(f)
    return {c['name']: c['value'] for c in cookie_list if 'name' in c and 'value' in c}

async def send_request(session, cookies, anon_id, req_id):
    cookies['anon_attempt_id'] = anon_id
    send_time = time.perf_counter()
    async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=HEADERS, cookies=cookies) as resp:
        recv_time = time.perf_counter()
        text = await resp.text()
        print(f"Req{req_id}: sent at {send_time:.6f}, recv at {recv_time:.6f}, status {resp.status}")
        print(f"  Response: {text}")
        return resp.status

async def main():
    print("🚀 Preparing 3 requests to fire simultaneously...")
    all_cookies = [load_cookies(cf) for cf in COOKIE_FILES]
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [send_request(session, all_cookies[i], ANON_IDS[i], i+1) for i in range(3)]
        print("Firing all requests simultaneously...")
        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        end = time.perf_counter()
        print(f"\nAll requests finished in {end - start:.4f} seconds.")
        print(f"Statuses: {results}")

if __name__ == "__main__":
    asyncio.run(main())