"""Create N perfect anon attempts (15/15) at chosen speed using cached answers."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from genius_1780164377809 import (
    load_cache, generate_attempt, validate_answer,
    run_probe_attempt, QUIZ_KEY, BASE_URL, HEADERS,
    aiohttp, random
)

async def verify_attempt(session, anon_id: str, retries: int = 4):
    """
    Call GET /api/attempt/:id and return {score, total, correct, unanswered, time}.
    Retries up to `retries` times with backoff on 429 or error.
    """
    for attempt in range(retries):
        try:
            async with session.get(
                f"{BASE_URL}/attempt/{anon_id}",
                headers=HEADERS,
                cookies={"anon_attempt_id": anon_id},
            ) as resp:
                if resp.status == 429:
                    wait = 5 * (attempt + 1)
                    print(f"         ⏳ Rate-limited (429) — waiting {wait}s before retry...")
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
                "score":      data.get("score", 0),
                "total":      total,
                "correct":    correct,
                "unanswered": unanswered,
                "time":       round(t_total, 1),
            }
        except Exception:
            await asyncio.sleep(3)
    return None

# Timing profiles — per-question sleep range (seconds)
PROFILES = {
    "1": {"label": "Under 30s  (~22 s avg)", "lo": 0.8,  "hi": 1.5},
    "2": {"label": "Under 50s  (~38 s avg)", "lo": 2.0,  "hi": 3.2},
}

async def create_perfect_attempt(session, quiz_cache, lo, hi):
    """Returns (anon_id, elapsed_s, correct, total) — score confirmed by server."""
    attempt_id, questions, anon_cookie = await generate_attempt(session)
    if not attempt_id:
        return None, None, 0, 0

    total_time = 0.0
    correct    = 0
    total      = len(questions)
    for i, q in enumerate(questions):
        is_last = (i == total - 1)
        answer  = quiz_cache.get(q["_id"]) or q["options"][0]
        t       = round(random.uniform(lo, hi), 2)
        total_time += t
        result = await validate_answer(
            session, {}, attempt_id, q, answer, t,
            total_time_used=round(total_time, 2) if is_last else None,
        )
        if result is True:
            correct += 1
        await asyncio.sleep(t)

    return anon_cookie, round(total_time, 1), correct, total


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
                print(f"  ⚠️  {len(remaining)} still uncached — those will use first option as fallback.")
            else:
                print(f"  ✅  All questions covered.")
            print("  ⏳ Cooling down 4s after probes...\n")
            await asyncio.sleep(4)   # let server settle before scored attempts

        # ── Create attempts ───────────────────────────────────────────────────
        anon_ids    = []
        tries       = 0
        max_tries   = target * 4
        did_gapfill = bool(missing)   # remember if probes ran this session

        while len(anon_ids) < target and tries < max_tries:
            tries += 1
            num = len(anon_ids) + 1
            print(f"  [{num}/{target}] Playing quiz...")
            aid, elapsed, _, _ = await create_perfect_attempt(session, quiz_cache, lo, hi)
            if aid:
                if did_gapfill:
                    # Server rate-limits verify after gap-fill probes — skip inline,
                    # use /verify <id> on the Telegram bot or _verify.py later.
                    anon_ids.append((aid, elapsed, "—"))
                    print(f"         ✅  {aid}  ({elapsed}s)  (verify later — see note)\n")
                else:
                    await asyncio.sleep(2)
                    v = await verify_attempt(session, aid)
                    if v:
                        score = f"{v['correct']}/{v['total']}"
                        flag  = "✅" if v["correct"] == v["total"] else "⚠️"
                        note  = "" if v["unanswered"] == 0 else f"  ({v['unanswered']} unanswered)"
                    else:
                        score, flag, note = "?", "❓", "  (verify failed — try /verify later)"
                    anon_ids.append((aid, elapsed, score))
                    print(f"         {flag}  {aid}  ({elapsed}s)  server: {score} correct{note}\n")
            else:
                print(f"         ⚠️  Connection hiccup, retrying in 2s...\n")
                await asyncio.sleep(2)

        if not anon_ids:
            print("❌  Could not create any attempts. Check your network connection.")
            return

        # ── Results ───────────────────────────────────────────────────────────
        sep = "=" * 62
        print(sep)
        print(f"  Generated {len(anon_ids)}/{target} anon IDs:\n")
        for aid, elapsed, score in anon_ids:
            if score == "—":
                mark = "✅"
                label = "verify with /verify later"
            else:
                parts = score.split("/")
                mark  = "✅" if len(parts) == 2 and parts[0] == parts[1] else "⚠️"
                label = f"{score} correct"
            print(f"    {aid}  ({elapsed}s)  {mark} {label}")
        print(f"\n  Paste all of these into main script → [3] Manual link")
        print(f"  (space-separated on one line is fine)\n")
        if did_gapfill:
            print(f"  ⚠️  Gap-fill probes ran — verify score after 5+ mins:")
            print(f"  Telegram: /verify <id>   or   python3 _verify.py <id>")
        print(f"\n  One-liner to copy:")
        print(f"  {' '.join(a for a, _, _ in anon_ids)}")
        print(sep)


asyncio.run(main())
