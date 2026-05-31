"""
Manual link tool — select a saved cookie, paste up to 3 anon IDs,
fire all link requests simultaneously, and compare score before vs after.
"""
import asyncio
import os
import sys
import time
import json

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genius_1780164377809 import (
    load_cookies, fetch_stats, send_link_request,
    BASE_URL, HEADERS, aiohttp
)

EXCLUDE = {"answers_cache.json", "correct_answers_today.json"}


# ─── Cookie file discovery ────────────────────────────────────────────────────

def find_cookie_files():
    """Return list of (nickname, filepath) for every *.json cookie file."""
    files = []
    for fname in sorted(os.listdir(".")):
        if fname.endswith(".json") and fname not in EXCLUDE:
            nickname = fname[:-5]
            files.append((nickname, fname))
    return files


def pick_cookie_file(files):
    """Interactive selection — returns (nickname, filepath)."""
    if not files:
        print("  ❌  No cookie files found in this directory.")
        print("       Run the main script and paste your cookie JSON first.")
        return None, None

    print("  Saved accounts:\n")
    for i, (nick, _) in enumerate(files, 1):
        print(f"    [{i}] {nick}")
    print()

    raw = input("  Select by number or type nickname: ").strip()

    # Numeric selection
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(files):
            return files[idx]
        print("  ⚠️  Invalid number.")
        return None, None

    # Nickname match
    raw_lower = raw.lower()
    for nick, path in files:
        if nick.lower() == raw_lower:
            return nick, path

    # Try as a bare filename
    candidate = raw if raw.endswith(".json") else raw + ".json"
    if os.path.exists(candidate) and candidate not in EXCLUDE:
        return candidate[:-5], candidate

    print(f"  ⚠️  No cookie file found for '{raw}'.")
    return None, None


# ─── Stat display ─────────────────────────────────────────────────────────────

def fmt(val):
    return str(val) if val is not None else "—"


def show_stats(label, elo, played, score):
    print(f"  {label}")
    print(f"    ELO score        : {fmt(elo)}")
    print(f"    Challenges played: {fmt(played)}")
    if score is not None:
        print(f"    Today's score    : {fmt(score)}")
    print()


async def fetch_full_stats(session, cookies):
    """Fetch ELO, challenges played, and today's quiz score."""
    elo, played = await fetch_stats(session, cookies)

    # Try to get today's score from the quiz history endpoints
    score = None
    for ep in ["/user/quiz-history", "/quiz/history", "/attempt/history",
               "/attempt/me", "/user/attempts"]:
        try:
            async with session.get(
                f"{BASE_URL}{ep}", headers=HEADERS, cookies=cookies
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data and isinstance(data, dict):
                        # Look for today's score anywhere in the response
                        from genius_1780164377809 import find_value
                        s = find_value(data, "score", "totalScore", "todayScore")
                        if s is not None:
                            score = s
                            break
        except Exception:
            pass

    return elo, played, score


# ─── Link logic ───────────────────────────────────────────────────────────────

async def run(nickname, filepath, anon_ids):
    cookies = load_cookies(filepath)
    print(f"\n  Loaded {len(cookies)} cookies for '{nickname}'.\n")

    connector = aiohttp.TCPConnector(limit=20, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Stats BEFORE ─────────────────────────────────────────────────────
        print("  📊  Fetching stats BEFORE submission...")
        elo_b, played_b, score_b = await fetch_full_stats(session, cookies)
        show_stats("Before:", elo_b, played_b, score_b)

        # ── Fire all link requests simultaneously ─────────────────────────────
        sep = "─" * 58
        print(sep)
        print(f"  🚀  Firing {len(anon_ids)} link request(s) simultaneously...\n")
        t0 = time.perf_counter()
        results = await asyncio.gather(*[
            send_link_request(session, cookies, anon_id, i + 1)
            for i, anon_id in enumerate(anon_ids)
        ])
        elapsed = time.perf_counter() - t0
        print(f"\n  Done in {elapsed:.3f}s")
        print(f"  HTTP statuses: {results}")
        ok    = sum(1 for s in results if s == 200)
        fails = len(results) - ok
        if fails:
            print(f"  ⚠️  {fails} request(s) did not return 200 — may still have linked.")
        print(sep)

        # ── Stats AFTER ──────────────────────────────────────────────────────
        print("\n  ⏳  Waiting 2s for server to process links...")
        await asyncio.sleep(2)
        print("  📊  Fetching stats AFTER submission...\n")
        elo_a, played_a, score_a = await fetch_full_stats(session, cookies)
        show_stats("After:", elo_a, played_a, score_a)

        # ── Delta summary ─────────────────────────────────────────────────────
        print("  ✨  Changes:")
        if elo_b is not None and elo_a is not None:
            d = elo_a - elo_b
            arrow = f"{'+'if d >= 0 else ''}{d}"
            print(f"    ELO score        : {fmt(elo_b)} → {fmt(elo_a)}  ({arrow})")
        elif elo_a is not None:
            print(f"    ELO score        : {fmt(elo_a)}")

        if played_b is not None and played_a is not None:
            d = played_a - played_b
            arrow = f"{'+'if d >= 0 else ''}{d}"
            print(f"    Challenges played: {fmt(played_b)} → {fmt(played_a)}  ({arrow})")
        elif played_a is not None:
            print(f"    Challenges played: {fmt(played_a)}")

        if score_a is not None:
            print(f"    Today's score    : {fmt(score_b)} → {fmt(score_a)}")

        if elo_b is None and played_b is None and elo_a is None and played_a is None:
            print("    (Could not fetch stats — check cookies are valid)")
        print()


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main():
    print("=" * 58)
    print("  Manual Link Tool — India Genius Challenge")
    print("=" * 58)
    print()

    # ── Select cookie ─────────────────────────────────────────────────────────
    files = find_cookie_files()
    nickname, filepath = pick_cookie_file(files)
    if not nickname:
        return

    # ── Enter anon IDs ────────────────────────────────────────────────────────
    print()
    while True:
        raw = input("  Paste anon IDs (space-separated, max 3): ").strip()
        ids = raw.split()
        if not ids:
            print("  ⚠️  Enter at least one ID.")
            continue
        if len(ids) > 3:
            print(f"  ⚠️  Max 3 IDs (got {len(ids)}). Using first 3.")
            ids = ids[:3]
        break

    print(f"\n  Will link {len(ids)} ID(s) to account '{nickname}':")
    for aid in ids:
        print(f"    • {aid}")

    await run(nickname, filepath, ids)


asyncio.run(main())
