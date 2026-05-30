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

def find_value(data, *keys):
    """Recursively search a dict/list for the first matching key."""
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                return data[k]
        for v in data.values():
            result = find_value(v, *keys)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_value(item, *keys)
            if result is not None:
                return result
    return None

async def fetch_stats(session, cookies):
    """Fetch ELO and total challenges from the user profile endpoint."""
    for endpoint in ["/user/me", "/me", "/user/profile", "/profile"]:
        try:
            async with session.get(f"{BASE_URL}{endpoint}", headers=HEADERS, cookies=cookies) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    elo = find_value(data, "elo", "eloScore", "elo_score", "rating")
                    played = find_value(data, "totalChallengesPlayed", "total_challenges_played",
                                       "challengesPlayed", "challenges_played", "gamesPlayed",
                                       "totalGames", "total_games", "played")
                    if elo is not None or played is not None:
                        return elo, played, data
        except Exception:
            pass
    return None, None, None

async def send_request(session, cookies, anon_id, req_id):
    c = dict(cookies)
    c['anon_attempt_id'] = anon_id
    send_time = time.perf_counter()
    async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=HEADERS, cookies=c) as resp:
        recv_time = time.perf_counter()
        text = await resp.text()
        print(f"  Req{req_id}: status {resp.status} | {recv_time - send_time:.3f}s | {text}")
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
    while True:
        raw_ids = input("Paste anon_attempt_id values separated by spaces (max 3): ").strip()
        anon_ids = raw_ids.split()
        if len(anon_ids) == 0:
            print("  ⚠️  Please enter at least one ID.")
            continue
        if len(anon_ids) > 3:
            print(f"  ⚠️  Too many IDs ({len(anon_ids)}). Max is 3. Please try again.")
            continue
        break

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:

        # --- Fetch stats BEFORE ---
        print("\nFetching your stats before...")
        elo_before, played_before, _ = await fetch_stats(session, cookies)
        if elo_before is not None or played_before is not None:
            print(f"  ELO: {elo_before}  |  Challenges played: {played_before}")
        else:
            print("  (Could not fetch stats before — will still show after)")

        # --- Fire requests ---
        print(f"\n🚀 Firing {len(anon_ids)} requests simultaneously...")
        start = time.perf_counter()
        tasks = [send_request(session, cookies, anon_ids[i], i+1) for i in range(len(anon_ids))]
        results = await asyncio.gather(*tasks)
        end = time.perf_counter()
        print(f"Done in {end - start:.4f}s | Statuses: {results}")

        # --- Fetch stats AFTER (small delay to let server update) ---
        await asyncio.sleep(1.5)
        print("\nFetching your stats after...")
        elo_after, played_after, raw_data = await fetch_stats(session, cookies)

        if elo_after is not None or played_after is not None:
            print(f"  ELO: {elo_after}  |  Challenges played: {played_after}")
            print("\n📊 Changes:")
            if elo_before is not None and elo_after is not None:
                diff_elo = elo_after - elo_before
                sign = "+" if diff_elo >= 0 else ""
                print(f"  ELO score      : {elo_before} → {elo_after}  ({sign}{diff_elo})")
            if played_before is not None and played_after is not None:
                diff_played = played_after - played_before
                sign = "+" if diff_played >= 0 else ""
                print(f"  Challenges played: {played_before} → {played_after}  ({sign}{diff_played})")
        else:
            print("  ⚠️  Could not fetch stats. Raw profile response (to help identify fields):")
            print(f"  {json.dumps(raw_data, indent=2) if raw_data else 'No response'}")

if __name__ == "__main__":
    asyncio.run(main())
