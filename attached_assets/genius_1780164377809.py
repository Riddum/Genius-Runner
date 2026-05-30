import asyncio
import aiohttp
import json
import time
import os

BASE_URL = "https://www.indiageniuschallenge.com/api"

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

def save_cookies(file_path, raw_text):
    data = json.loads(raw_text)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"✅ Cookies saved to {file_path}")

async def send_request(session, cookies, anon_id, req_id):
    c = dict(cookies)
    c['anon_attempt_id'] = anon_id
    send_time = time.perf_counter()
    async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=HEADERS, cookies=c) as resp:
        recv_time = time.perf_counter()
        text = await resp.text()
        print(f"Req{req_id}: sent at {send_time:.6f}, recv at {recv_time:.6f}, status {resp.status}")
        print(f"  Response: {text}")
        return resp.status

async def main():
    print("=== India Genius Challenge — Simultaneous Request Sender ===\n")

    # --- Cookie setup ---
    nickname = input("Enter a nickname for your cookie file (e.g. myaccount): ").strip()
    cookie_file = f"{nickname}.json"

    if os.path.exists(cookie_file):
        print(f"✅ Found existing cookie file: {cookie_file}")
        use_existing = input("Use it? (y/n): ").strip().lower()
        if use_existing != 'y':
            os.remove(cookie_file)

    if not os.path.exists(cookie_file):
        print("\nPaste your cookie JSON below (from EditThisCookie or browser export).")
        print("Press Enter twice when done:\n")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        raw = "\n".join(lines).strip()
        save_cookies(cookie_file, raw)

    cookies = load_cookies(cookie_file)
    print(f"Loaded {len(cookies)} cookies.\n")

    # --- Anon IDs ---
    print("Enter anon_attempt_id values one per line (press Enter on an empty line when done):")
    anon_ids = []
    while True:
        val = input(f"  Anon ID #{len(anon_ids)+1} (or blank to finish): ").strip()
        if val == "":
            if len(anon_ids) == 0:
                print("  ⚠️  Please enter at least one ID.")
                continue
            break
        anon_ids.append(val)

    print(f"\n🚀 Preparing {len(anon_ids)} requests to fire simultaneously...")
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [send_request(session, cookies, anon_ids[i], i+1) for i in range(len(anon_ids))]
        print("Firing all requests simultaneously...")
        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        end = time.perf_counter()
        print(f"\nAll requests finished in {end - start:.4f} seconds.")
        print(f"Statuses: {results}")

if __name__ == "__main__":
    asyncio.run(main())
