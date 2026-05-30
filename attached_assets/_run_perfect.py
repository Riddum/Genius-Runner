"""Create 3 perfect anon attempts (15/15, <30s) using cached answers."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from genius_1780164377809 import (
    load_cache, generate_attempt, validate_answer,
    discover_for_attempt, QUIZ_KEY, HEADERS,
    aiohttp, random
)

async def create_perfect_anon_noauth(session, quiz_cache, question_meta):
    attempt_id, questions, anon_cookie = await generate_attempt(session)
    if not attempt_id:
        return None, None

    total_time = 0
    for i, q in enumerate(questions):
        is_last = (i == len(questions) - 1)
        answer  = quiz_cache.get(q["_id"]) or q["options"][0]
        t       = round(random.uniform(1.0, 2.0), 2)
        total_time += t
        await validate_answer(
            session, {}, attempt_id, q, answer, t,
            total_time_used=round(total_time, 2) if is_last else None,
        )
        await asyncio.sleep(t)

    return anon_cookie, round(total_time, 1)

async def main():
    print("=== Creating 3 perfect anonymous attempts ===\n")
    cache         = load_cache()
    quiz_cache    = cache.get(QUIZ_KEY, {})
    question_meta = cache.get("question_meta", {})

    if not quiz_cache:
        print("❌ No cached answers found. Run mode [1] first to collect answers.")
        return

    print(f"  Using {len(quiz_cache)} cached answers.\n")

    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:

        # Ensure today's 15 questions are all covered
        _, ref_questions, _ = await generate_attempt(session)
        if not ref_questions:
            print("  ❌ Could not fetch today's quiz.")
            return

        missing = [q for q in ref_questions if q["_id"] not in quiz_cache]
        if missing:
            print(f"  ⚠️  {len(missing)} question(s) not in cache — running quick probes...")
            from genius_1780164377809 import run_probe_attempt
            tried = {}
            sem   = asyncio.Semaphore(3)
            for _ in range(4):
                still = [q for q in ref_questions if q["_id"] not in quiz_cache]
                if not still: break
                await run_probe_attempt(session, quiz_cache, question_meta, tried, sem, 0)

        anon_ids = []
        attempt_num = 0
        while len(anon_ids) < 3:
            attempt_num += 1
            print(f"  Attempt {len(anon_ids)+1}/3 — playing quiz...")
            aid, elapsed = await create_perfect_anon_noauth(session, quiz_cache, question_meta)
            if aid:
                anon_ids.append(aid)
                print(f"  ✅  anon_attempt_id: {aid}  ({elapsed}s)\n")
            else:
                print(f"  ⚠️  Connection error, retrying...\n")
                await asyncio.sleep(2)
            if attempt_num > 9:
                print("  ❌ Too many failures, stopping.")
                break

        print("=" * 55)
        print("  COPY THESE — paste into mode [3] to link to your account:")
        print("  " + " ".join(anon_ids))
        print("=" * 55)

asyncio.run(main())
