"""Verify any anon attempt ID(s) — pass IDs as command-line args or type them."""
import asyncio, sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from genius_1780164377809 import BASE_URL, HEADERS, aiohttp

async def verify_attempt(session, anon_id: str, retries: int = 4):
    for attempt in range(retries):
        try:
            async with session.get(
                f"{BASE_URL}/attempt/{anon_id}",
                headers=HEADERS,
                cookies={"anon_attempt_id": anon_id},
            ) as resp:
                if resp.status == 429:
                    wait = 5 * (attempt + 1)
                    print(f"  ⏳ Rate-limited (429) — waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                data = await resp.json(content_type=None)
            if not data or not data.get("success"):
                await asyncio.sleep(3)
                continue
            attempted  = data.get("attemptedQuestions", [])
            total      = data.get("totalQuestions", 15)
            correct    = sum(1 for e in attempted if e.get("isCorrect"))
            unanswered = sum(1 for e in attempted if not e.get("selectedOption"))
            t_total    = data.get("attemptData", {}).get("timeTakenTotal", 0)
            return {
                "total":      total,
                "correct":    correct,
                "unanswered": unanswered,
                "time":       round(t_total, 1),
                "attempted":  attempted,
            }
        except Exception:
            await asyncio.sleep(3)
    return None

async def main():
    ids = sys.argv[1:]
    if not ids:
        raw = input("Enter anon attempt ID(s) (space-separated): ").strip()
        ids = raw.split()
    if not ids:
        print("No IDs provided. Exiting.")
        return

    connector = aiohttp.TCPConnector(limit=5, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for anon_id in ids:
            print(f"\n🔍 Verifying {anon_id} ...")
            v = await verify_attempt(session, anon_id)
            if not v:
                print(f"  ❌  Could not fetch — ID invalid, expired, or still rate-limited.")
                continue

            icon = "✅" if v["correct"] == v["total"] else "⚠️"
            print(f"  {icon}  Score : {v['correct']}/{v['total']} correct")
            print(f"  ⏱   Time  : {v['time']}s")
            if v["unanswered"]:
                print(f"  ⚠️   Unanswered questions: {v['unanswered']}")
                print(f"       (These had empty submissions — likely due to rate-limiting during creation)")
            print()

            # Per-question breakdown
            print("  Per-question breakdown:")
            for e in v["attempted"]:
                mark = "✅" if e.get("isCorrect") else ("❌" if e.get("selectedOption") else "⬜")
                sel  = e.get("selectedOption") or "(none)"
                print(f"    {mark}  {e['questionId'][:12]}…  →  {sel}")

asyncio.run(main())
