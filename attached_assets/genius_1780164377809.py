import asyncio
import aiohttp
import json
import time
import os
import random

BASE_URL = "https://www.indiageniuschallenge.com/api"
CACHE_FILE = "answers_cache.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.indiageniuschallenge.com/quiz",
    "Origin": "https://www.indiageniuschallenge.com",
    "Content-Type": "application/json",
}

# ─── Cookie helpers ──────────────────────────────────────────────────────────

def load_cookies(file_path):
    with open(file_path, 'r') as f:
        cookie_list = json.load(f)
    return {c['name']: c['value'] for c in cookie_list if 'name' in c and 'value' in c}

def save_cookies(file_path, raw_text):
    data = json.loads(raw_text)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  ✅ Cookies saved to {file_path}")

# ─── Answer cache ─────────────────────────────────────────────────────────────

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

# ─── API helpers ──────────────────────────────────────────────────────────────

async def generate_attempt(session, cookies):
    """Create a new anonymous attempt. Returns (attempt_id, questions, anon_cookie)."""
    jar = aiohttp.CookieJar(unsafe=True)
    for k, v in cookies.items():
        jar.update_cookies({k: v})
    async with session.post(
        f"{BASE_URL}/attempt/generate",
        headers=HEADERS,
        cookies=cookies,
        json={},
    ) as resp:
        # Grab the new anon_attempt_id from Set-Cookie
        anon_cookie = None
        for morsel in resp.cookies.values():
            if morsel.key == "anon_attempt_id":
                anon_cookie = morsel.value
                break
        data = await resp.json(content_type=None)
        if not data.get("success") or not data.get("data"):
            return None, None, None
        quiz  = data["data"]["quiz"]
        attempt = data["data"]["attempt"]
        return attempt["_id"], quiz["Questions"], anon_cookie

async def validate_answer(session, cookies, attempt_id, question, selected_answer, time_spent, total_time_used=None):
    """Submit one answer. Returns isCorrect bool."""
    payload = {
        "_id": attempt_id,
        "questionId": question["_id"],
        "question": question["question"],
        "selectedAnswer": selected_answer,
        "timeSpent": time_spent,
    }
    if total_time_used is not None:
        payload["totalTimeUsed"] = total_time_used
    async with session.post(
        f"{BASE_URL}/attempt/validate",
        headers=HEADERS,
        cookies=cookies,
        json=payload,
    ) as resp:
        data = await resp.json(content_type=None)
        if data.get("duplicateSubmission"):
            return None  # already answered this question
        attempted = data.get("data", {}).get("QuestionsAttempted", [])
        for entry in attempted:
            if entry.get("questionId") == question["_id"]:
                return entry.get("isCorrect", False)
        return False

# ─── Stats helpers ────────────────────────────────────────────────────────────

def find_value(data, *keys):
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                return data[k]
        for v in data.values():
            r = find_value(v, *keys)
            if r is not None:
                return r
    elif isinstance(data, list):
        for item in data:
            r = find_value(item, *keys)
            if r is not None:
                return r
    return None

async def fetch_stats(session, cookies):
    for ep in ["/user/me", "/me", "/user/profile", "/profile"]:
        try:
            async with session.get(f"{BASE_URL}{ep}", headers=HEADERS, cookies=cookies) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    elo    = find_value(data, "elo", "eloScore", "elo_score", "rating")
                    played = find_value(data, "totalChallengesPlayed", "total_challenges_played",
                                        "challengesPlayed", "challenges_played",
                                        "gamesPlayed", "totalGames", "total_games", "played")
                    if elo is not None or played is not None:
                        return elo, played
        except Exception:
            pass
    return None, None

# ─── Core: discover correct answers via probing ───────────────────────────────

async def discover_answers(session, cookies, questions, quiz_id, cache):
    """
    Find the correct option for each question.
    Uses cached answers first; probes the API for unknowns.
    Returns dict {question_id: correct_option_text}.
    """
    quiz_cache = cache.get(quiz_id, {})
    unknown    = [q for q in questions if q["_id"] not in quiz_cache]

    if not unknown:
        print("  ✅ All answers already cached.")
        return quiz_cache

    print(f"  🔍 Probing {len(unknown)} unknown question(s) — this creates temporary attempts...")
    option_index = 0  # which option index to test in this probe round

    while unknown and option_index < 4:
        print(f"  Probe round {option_index + 1}: testing option index {option_index} on {len(unknown)} question(s)...")
        attempt_id, probe_questions, _ = await generate_attempt(session, cookies)
        if not attempt_id:
            print("  ⚠️  Could not create probe attempt.")
            break

        # Build a lookup from question_id → question object for this attempt
        probe_map = {q["_id"]: q for q in probe_questions}
        still_unknown = []

        # Submit answers for all 15 questions in this probe attempt
        total_time = 0
        for i, q in enumerate(probe_questions):
            qid = q["_id"]
            is_last = (i == len(probe_questions) - 1)

            if qid in quiz_cache:
                # Already know correct answer — fill with it (correct or not doesn't matter)
                option = quiz_cache[qid]
            else:
                orig_q = next((x for x in unknown if x["_id"] == qid), None)
                if orig_q and option_index < len(orig_q["options"]):
                    option = orig_q["options"][option_index]
                else:
                    option = q["options"][0]  # fallback

            t = random.randint(1, 3)
            total_time += t
            is_correct = await validate_answer(
                session, cookies, attempt_id, q, option, t,
                total_time_used=total_time if is_last else None
            )

            if qid not in quiz_cache:
                if is_correct:
                    quiz_cache[qid] = option
                    print(f"    ✓ Q{i+1}: \"{option}\"")
                else:
                    still_unknown.append(next((x for x in unknown if x["_id"] == qid), q))

            await asyncio.sleep(0.05)  # small delay between submissions

        unknown = still_unknown
        option_index += 1

    if unknown:
        print(f"  ⚠️  Could not determine correct answers for {len(unknown)} question(s).")

    cache[quiz_id] = quiz_cache
    save_cache(cache)
    print(f"  💾 Answers cached for future use.")
    return quiz_cache

# ─── Core: create perfect anon attempt ───────────────────────────────────────

async def create_perfect_anon(session, cookies, correct_answers, questions):
    """
    Create a fresh anonymous attempt and submit all correct answers
    in under 30 seconds total.
    Returns the anon_attempt_id cookie value.
    """
    print("\n  🎯 Creating final attempt with all correct answers...")
    attempt_id, attempt_questions, anon_cookie = await generate_attempt(session, cookies)
    if not attempt_id:
        print("  ❌ Failed to create final attempt.")
        return None

    total_time = 0
    for i, q in enumerate(attempt_questions):
        is_last = (i == len(attempt_questions) - 1)
        answer  = correct_answers.get(q["_id"])
        if not answer:
            answer = q["options"][0]
            print(f"  ⚠️  Q{i+1}: no cached answer, using first option as fallback.")

        t = random.uniform(1.0, 2.5)   # 1–2.5 seconds per question → ~15–37s total
        total_time += t

        await validate_answer(
            session, cookies, attempt_id, q, answer, round(t, 2),
            total_time_used=round(total_time, 2) if is_last else None
        )
        await asyncio.sleep(t)  # simulate realistic pacing

    print(f"  ⏱️  Completed in {total_time:.1f}s total.")
    return anon_cookie

# ─── Linked-account request sender ───────────────────────────────────────────

async def send_link_request(session, cookies, anon_id, req_id):
    c = dict(cookies)
    c["anon_attempt_id"] = anon_id
    send_time = time.perf_counter()
    async with session.get(
        f"{BASE_URL}/attempt/linkAnon",
        headers=HEADERS,
        cookies=c,
    ) as resp:
        recv_time = time.perf_counter()
        text = await resp.text()
        print(f"  Req{req_id}: status {resp.status} | {recv_time - send_time:.3f}s | {text}")
        return resp.status

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=== India Genius Challenge — Auto Quiz & Request Sender ===\n")

    # ── Cookie setup ──────────────────────────────────────────────────────────
    nickname    = input("Enter a nickname for your cookie file (e.g. myaccount): ").strip()
    cookie_file = f"{nickname}.json"

    if os.path.exists(cookie_file):
        print(f"  ✅ Found existing cookie file: {cookie_file}")
        if input("  Use it? (y/n): ").strip().lower() != "y":
            os.remove(cookie_file)

    if not os.path.exists(cookie_file):
        print("\n  Paste your cookie JSON below (from EditThisCookie). Press Enter twice when done:\n")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        save_cookies(cookie_file, "\n".join(lines).strip())

    cookies = load_cookies(cookie_file)
    print(f"  Loaded {len(cookies)} cookies.\n")

    # ── Mode selection ────────────────────────────────────────────────────────
    print("How do you want to provide anon IDs?")
    print("  [1] Auto-create (play quiz, get 15/15 in <30s)")
    print("  [2] Enter manually (paste space-separated IDs)")
    mode = input("Choice (1 or 2): ").strip()

    anon_ids = []

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:

        if mode == "1":
            count = input("How many anon IDs to create (1–3)? ").strip()
            count = max(1, min(3, int(count) if count.isdigit() else 1))

            print(f"\n📚 Step 1: Discovering correct answers for today's quiz...")
            cache = load_cache()

            # Probe using a temp attempt to learn answers (only needs to happen once per quiz)
            _, probe_questions, _ = await generate_attempt(session, cookies)
            if not probe_questions:
                print("  ❌ Could not fetch today's quiz. Exiting.")
                return

            quiz_id = "daily"  # same quiz every day
            correct_answers = await discover_answers(session, cookies, probe_questions, quiz_id, cache)

            print(f"\n🎮 Step 2: Creating {count} perfect anon attempt(s)...")
            for i in range(count):
                print(f"\n  Attempt {i+1}/{count}:")
                anon_id = await create_perfect_anon(session, cookies, correct_answers, probe_questions)
                if anon_id:
                    anon_ids.append(anon_id)
                    print(f"  ✅ anon_attempt_id: {anon_id}")
                else:
                    print("  ❌ Failed to create attempt.")

        else:
            while True:
                raw = input("\nPaste anon_attempt_id values separated by spaces (max 3): ").strip()
                anon_ids = raw.split()
                if len(anon_ids) == 0:
                    print("  ⚠️  Please enter at least one ID.")
                    continue
                if len(anon_ids) > 3:
                    print(f"  ⚠️  Too many ({len(anon_ids)}). Max is 3.")
                    continue
                break

        if not anon_ids:
            print("\n❌ No anon IDs available. Exiting.")
            return

        # ── Fetch stats BEFORE ────────────────────────────────────────────────
        print(f"\n📊 Fetching your stats before linking...")
        elo_before, played_before = await fetch_stats(session, cookies)
        if elo_before is not None or played_before is not None:
            print(f"  ELO: {elo_before}  |  Challenges played: {played_before}")
        else:
            print("  (Could not fetch stats before)")

        # ── Fire link requests ────────────────────────────────────────────────
        print(f"\n🚀 Firing {len(anon_ids)} link request(s) simultaneously...")
        start = time.perf_counter()
        tasks   = [send_link_request(session, cookies, anon_ids[i], i+1) for i in range(len(anon_ids))]
        results = await asyncio.gather(*tasks)
        end = time.perf_counter()
        print(f"  Done in {end - start:.4f}s | Statuses: {results}")

        # ── Fetch stats AFTER ─────────────────────────────────────────────────
        await asyncio.sleep(1.5)
        print("\n📊 Fetching your stats after linking...")
        elo_after, played_after = await fetch_stats(session, cookies)

        if elo_after is not None or played_after is not None:
            print(f"  ELO: {elo_after}  |  Challenges played: {played_after}")
            print("\n✨ Changes:")
            if elo_before is not None and elo_after is not None:
                d = elo_after - elo_before
                print(f"  ELO score        : {elo_before} → {elo_after}  ({'+'if d>=0 else ''}{d})")
            if played_before is not None and played_after is not None:
                d = played_after - played_before
                print(f"  Challenges played: {played_before} → {played_after}  ({'+'if d>=0 else ''}{d})")
        else:
            print("  (Could not fetch stats after)")

if __name__ == "__main__":
    asyncio.run(main())
