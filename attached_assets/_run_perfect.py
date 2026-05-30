"""Create N perfect anon attempts (15/15) at chosen speed using cached answers."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from genius_1780164377809 import (
    load_cache, generate_attempt, validate_answer,
    run_probe_attempt, QUIZ_KEY,
    aiohttp, random
)

# Timing profiles — per-question sleep range (seconds)
PROFILES = {
    "1": {"label": "Under 30s  (~22 s avg)", "lo": 0.8,  "hi": 1.5},
    "2": {"label": "Under 50s  (~38 s avg)", "lo": 2.0,  "hi": 3.2},
}

async def create_perfect_attempt(session, quiz_cache, lo, hi):
    """Play one quiz using cached answers with timing in [lo, hi] per question."""
    attempt_id, questions, anon_cookie = await generate_attempt(session)
    if not attempt_id:
        return None, None

    total_time = 0.0
    for i, q in enumerate(questions):
        is_last = (i == len(questions) - 1)
        answer  = quiz_cache.get(q["_id"]) or q["options"][0]
        t       = round(random.uniform(lo, hi), 2)
        total_time += t
        await validate_answer(
            session, {}, attempt_id, q, answer, t,
            total_time_used=round(total_time, 2) if is_last else None,
        )
        await asyncio.sleep(t)

    return anon_cookie, round(total_time, 1)


async def main():
    print("=== Perfect Anon Attempt Generator ===\n")
    cache      = load_cache()
    quiz_cache = cache.get(QUIZ_KEY, {})
    question_meta = cache.get("question_meta", {})

    if not quiz_cache:
        print("❌  No cached answers found. Run the main script → [1] Refresh answer bank first.")
        return

    print(f"  Answer bank: {len(quiz_cache)} questions cached today.\n")

    # ── How many IDs? ─────────────────────────────────────────────────────────
    raw = input("  How many anon IDs to generate? (default 3): ").strip()
    target = int(raw) if raw.isdigit() and int(raw) > 0 else 3

    # ── Speed profile ─────────────────────────────────────────────────────────
    print()
    for k, p in PROFILES.items():
        print(f"    [{k}] {p['label']}")
    raw = input("\n  Choose speed (1 / 2, default 1): ").strip()
    profile = PROFILES.get(raw, PROFILES["1"])
    lo, hi  = profile["lo"], profile["hi"]
    print(f"\n  Using profile: {profile['label']}\n")

    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Quick gap-fill if today's draw has uncached questions ─────────────
        _, ref_questions, _ = await generate_attempt(session)
        if not ref_questions:
            print("  ❌  Could not reach the quiz server.")
            return

        missing = [q for q in ref_questions if q["_id"] not in quiz_cache]
        if missing:
            print(f"  ⚠️  {len(missing)} question(s) not in cache — running quick gap-fill probes...")
            tried = {}
            sem   = asyncio.Semaphore(3)
            for run in range(8):
                still = [q for q in ref_questions if q["_id"] not in quiz_cache]
                if not still:
                    break
                await run_probe_attempt(session, quiz_cache, question_meta, tried, sem, run)
            remaining = [q for q in ref_questions if q["_id"] not in quiz_cache]
            if remaining:
                print(f"  ⚠️  {len(remaining)} still uncached — those will use first option as fallback.\n")
            else:
                print(f"  ✅  All questions covered.\n")

        # ── Create attempts ───────────────────────────────────────────────────
        anon_ids   = []
        tries      = 0
        max_tries  = target * 4

        while len(anon_ids) < target and tries < max_tries:
            tries += 1
            num = len(anon_ids) + 1
            print(f"  [{num}/{target}] Playing quiz...")
            aid, elapsed = await create_perfect_attempt(session, quiz_cache, lo, hi)
            if aid:
                anon_ids.append(aid)
                print(f"         ✅  {aid}  ({elapsed}s)\n")
            else:
                print(f"         ⚠️  Connection hiccup, retrying in 2s...\n")
                await asyncio.sleep(2)

        if not anon_ids:
            print("❌  Could not create any attempts. Check your network connection.")
            return

        # ── Results ───────────────────────────────────────────────────────────
        sep = "=" * 58
        print(sep)
        print(f"  Generated {len(anon_ids)}/{target} perfect anon IDs:\n")
        for aid in anon_ids:
            print(f"    {aid}")
        print(f"\n  Paste all of these into main script → [3] Manual link")
        print(f"  (space-separated on one line is fine)\n")
        print(f"  One-liner to copy:")
        print(f"  {' '.join(anon_ids)}")
        print(sep)


asyncio.run(main())
