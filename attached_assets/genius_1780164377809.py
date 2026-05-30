import asyncio
import aiohttp
import json
import time
import os
import random
from datetime import date

BASE_URL   = "https://www.indiageniuschallenge.com/api"
CACHE_FILE = "answers_cache.json"
QUIZ_KEY   = f"daily_{date.today().isoformat()}"   # key rotates each day

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.indiageniuschallenge.com/quiz",
    "Origin": "https://www.indiageniuschallenge.com",
    "Content-Type": "application/json",
}

# ─── Cookie helpers ───────────────────────────────────────────────────────────

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

async def generate_attempt(session, cookies=None):
    """Create a new anonymous attempt (no auth cookies required).
    Returns (attempt_id, questions, anon_cookie_value)."""
    async with session.post(
        f"{BASE_URL}/attempt/generate",
        headers=HEADERS,
        cookies=cookies or {},
        json={},
    ) as resp:
        anon_cookie = None
        for morsel in resp.cookies.values():
            if morsel.key == "anon_attempt_id":
                anon_cookie = morsel.value
                break
        data = await resp.json(content_type=None)
        if not data.get("success") or not data.get("data"):
            return None, None, None
        quiz    = data["data"]["quiz"]
        attempt = data["data"]["attempt"]
        return attempt["_id"], quiz["Questions"], anon_cookie

async def validate_answer(session, cookies, attempt_id, question,
                          selected_answer, time_spent, total_time_used=None):
    """Submit one answer. Returns isCorrect (True/False) or None if duplicate."""
    payload = {
        "_id":            attempt_id,
        "questionId":     question["_id"],
        "question":       question["question"],
        "selectedAnswer": selected_answer,
        "timeSpent":      time_spent,
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
            return None
        for entry in data.get("data", {}).get("QuestionsAttempted", []):
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
                    played = find_value(data, "totalChallengesPlayed",
                                        "total_challenges_played", "challengesPlayed",
                                        "challenges_played", "gamesPlayed",
                                        "totalGames", "total_games", "played")
                    if elo is not None or played is not None:
                        return elo, played
        except Exception:
            pass
    return None, None

# ─── Single probe attempt ─────────────────────────────────────────────────────

async def run_probe_attempt(session, quiz_cache, question_meta, tried_options, sem, run_num):
    """
    One probe attempt:
    - For known questions: submit correct answer.
    - For unknown questions: try the next untried option index.
    Updates quiz_cache, question_meta and tried_options in place.
    Returns number of NEW answers discovered.
    """
    async with sem:
        try:
            attempt_id, questions, _ = await generate_attempt(session)
        except Exception:
            await asyncio.sleep(2)
            return 0
        if not attempt_id:
            return 0

        new_found = 0
        total_time = 0

        for i, q in enumerate(questions):
            qid     = q["_id"]
            is_last = (i == len(questions) - 1)

            # Always store question metadata as we encounter it
            if qid not in question_meta:
                question_meta[qid] = {
                    "question":    q.get("question", ""),
                    "options":     q.get("options", []),
                    "category":    q.get("subCategory", ""),
                    "difficulty":  q.get("difficulty", ""),
                    "type":        q.get("type", "text"),
                }

            if qid in quiz_cache:
                option = quiz_cache[qid]
            else:
                tried   = tried_options.get(qid, set())
                untried = [idx for idx in range(len(q["options"])) if idx not in tried]
                if not untried:
                    option = q["options"][0]   # all tried, just fill
                else:
                    opt_idx = untried[0]
                    tried.add(opt_idx)
                    tried_options[qid] = tried
                    option = q["options"][opt_idx]

            t = random.uniform(0.8, 2.0)
            total_time += t
            try:
                is_correct = await validate_answer(
                    session, {}, attempt_id, q, option, round(t, 2),
                    total_time_used=round(total_time, 2) if is_last else None,
                )
            except Exception:
                is_correct = False

            if is_correct and qid not in quiz_cache:
                quiz_cache[qid] = option
                new_found += 1

            await asyncio.sleep(0.05)

        return new_found

# ─── Collection mode ──────────────────────────────────────────────────────────

async def collect_answers(session, num_runs=30, concurrency=5):
    """
    Run `num_runs` probe attempts (max `concurrency` at a time) to discover
    as many correct answers from the question pool as possible.
    Returns (quiz_cache, question_meta) dicts.
    """
    cache         = load_cache()
    quiz_cache    = dict(cache.get(QUIZ_KEY, {}))
    question_meta = dict(cache.get("question_meta", {}))
    tried_options = {}   # {question_id: set of tried option indices}
    sem           = asyncio.Semaphore(concurrency)

    print(f"\n  Starting with {len(quiz_cache)} already-cached answers.")
    print(f"  Running {num_runs} probe attempts (up to {concurrency} in parallel)...\n")

    completed = 0

    tasks = [
        run_probe_attempt(session, quiz_cache, question_meta, tried_options, sem, i + 1)
        for i in range(num_runs)
    ]

    for coro in asyncio.as_completed(tasks):
        await coro
        completed += 1
        bar = "█" * (completed * 30 // num_runs) + "░" * (30 - completed * 30 // num_runs)
        print(f"\r  [{bar}] {completed}/{num_runs} done  |  {len(quiz_cache)} answers cached", end="", flush=True)

    print(f"\n\n  ✅ Collection complete. Total unique answers cached: {len(quiz_cache)}")

    # Save back to disk
    cache[QUIZ_KEY]       = quiz_cache
    cache["question_meta"] = question_meta
    save_cache(cache)

    return quiz_cache, question_meta

# ─── Export human-readable answers file ──────────────────────────────────────

async def export_answers_file(session, quiz_cache, question_meta, out_path="correct_answers_today.json"):
    """Write a readable JSON file using the stored question metadata."""
    output = []
    for qid, correct_option in quiz_cache.items():
        meta = question_meta.get(qid, {})
        q_type = meta.get("type", "text")
        label  = "(image question)" if q_type in ("questionImage", "image") and not meta.get("question") else meta.get("question", "(unknown)")
        output.append({
            "question_id":    qid,
            "question":       label,
            "type":           q_type,
            "category":       meta.get("category", ""),
            "difficulty":     meta.get("difficulty", ""),
            "options":        meta.get("options", []),
            "correct_answer": correct_option,
        })

    output.sort(key=lambda x: x["question"])

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  📄 Answers file saved: {out_path}  ({len(output)} questions)")
    return out_path

# ─── Discover answers for today's specific quiz (for auto-play mode) ──────────

async def discover_for_attempt(session, questions, quiz_cache, question_meta):
    """
    Given today's specific 15 questions, ensure all have cached answers.
    Falls back to probing for any still unknown.
    """
    unknown = [q for q in questions if q["_id"] not in quiz_cache]
    if not unknown:
        return quiz_cache

    print(f"  🔍 {len(unknown)} question(s) not in cache — running targeted probes...")
    tried = {}
    sem   = asyncio.Semaphore(3)

    for opt_idx in range(4):
        still_unknown = [q for q in unknown if q["_id"] not in quiz_cache]
        if not still_unknown:
            break
        await run_probe_attempt(session, quiz_cache, question_meta, tried, sem, opt_idx)

    remaining = [q for q in questions if q["_id"] not in quiz_cache]
    if remaining:
        print(f"  ⚠️  {len(remaining)} still unknown — will use first option as fallback.")
    return quiz_cache

# ─── Create one perfect anon attempt ─────────────────────────────────────────

async def create_perfect_anon(session, cookies, quiz_cache, ref_questions):
    """
    Create a fresh attempt and submit all correct answers in <30 seconds.
    Returns the anon_attempt_id cookie value.
    """
    attempt_id, questions, anon_cookie = await generate_attempt(session, cookies)
    if not attempt_id:
        return None

    total_time = 0
    for i, q in enumerate(questions):
        is_last = (i == len(questions) - 1)
        answer  = quiz_cache.get(q["_id"]) or q["options"][0]

        t = random.uniform(1.0, 2.0)
        total_time += t

        await validate_answer(
            session, cookies, attempt_id, q, answer, round(t, 2),
            total_time_used=round(total_time, 2) if is_last else None,
        )
        await asyncio.sleep(t)

    print(f"    ⏱️  Completed in {total_time:.1f}s")
    return anon_cookie

# ─── Link request ────────────────────────────────────────────────────────────

async def send_link_request(session, cookies, anon_id, req_id):
    c = dict(cookies)
    c["anon_attempt_id"] = anon_id
    t0 = time.perf_counter()
    async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=HEADERS, cookies=c) as resp:
        text = await resp.text()
        print(f"  Req{req_id}: status {resp.status} | {time.perf_counter()-t0:.3f}s | {text}")
        return resp.status

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=== India Genius Challenge Bot ===\n")
    print("  [1] Collect answers  (run many probes to build the answer bank)")
    print("  [2] Auto-play & link (use cached answers, create perfect attempts)")
    print("  [3] Manual link      (paste your own anon IDs)")
    mode = input("\nChoice (1 / 2 / 3): ").strip()

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── MODE 1: Collect answers ───────────────────────────────────────────
        if mode == "1":
            raw = input("How many probe attempts to run? (default 30): ").strip()
            num_runs = int(raw) if raw.isdigit() else 30
            raw = input("Concurrency (default 5, max 10): ").strip()
            concurrency = min(10, int(raw) if raw.isdigit() else 5)

            quiz_cache, question_meta = await collect_answers(session, num_runs, concurrency)

            print("\nExporting readable answers file...")
            out = await export_answers_file(session, quiz_cache, question_meta)
            print(f"\n  Done! Open '{out}' to see all correct answers.")
            return

        # ── MODES 2 & 3: Need user cookies ───────────────────────────────────
        print()
        nickname    = input("Enter your account nickname: ").strip()
        cookie_file = f"{nickname}.json"

        if os.path.exists(cookie_file):
            print(f"  ✅ Found: {cookie_file}")
            if input("  Use it? (y/n): ").strip().lower() != "y":
                os.remove(cookie_file)

        if not os.path.exists(cookie_file):
            print("\n  Paste your cookie JSON (from EditThisCookie). Press Enter twice when done:\n")
            lines = []
            while True:
                line = input()
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            save_cookies(cookie_file, "\n".join(lines).strip())

        cookies = load_cookies(cookie_file)
        print(f"  Loaded {len(cookies)} cookies.\n")

        anon_ids = []

        # ── MODE 2: Auto-play ─────────────────────────────────────────────────
        if mode == "2":
            raw   = input("How many perfect attempts to create (1–3)? ").strip()
            count = max(1, min(3, int(raw) if raw.isdigit() else 1))

            cache      = load_cache()
            quiz_cache = dict(cache.get(QUIZ_KEY, {}))

            print("\n📚 Fetching today's questions...")
            _, ref_questions, _ = await generate_attempt(session)
            if not ref_questions:
                print("  ❌ Could not fetch quiz."); return

            question_meta = dict(cache.get("question_meta", {}))
            quiz_cache = await discover_for_attempt(session, ref_questions, quiz_cache, question_meta)
            cache["question_meta"] = question_meta
            cache[QUIZ_KEY] = quiz_cache
            save_cache(cache)

            print(f"\n🎮 Creating {count} perfect anon attempt(s)...")
            for i in range(count):
                print(f"\n  Attempt {i+1}/{count}:")
                aid = await create_perfect_anon(session, cookies, quiz_cache, ref_questions)
                if aid:
                    anon_ids.append(aid)
                    print(f"    ✅ anon_attempt_id: {aid}")
                else:
                    print("    ❌ Failed.")

        # ── MODE 3: Manual IDs ────────────────────────────────────────────────
        else:
            while True:
                raw = input("\nPaste anon IDs separated by spaces (max 3): ").strip()
                ids = raw.split()
                if not ids:
                    print("  ⚠️  Enter at least one."); continue
                if len(ids) > 3:
                    print(f"  ⚠️  Max 3 (got {len(ids)})."); continue
                anon_ids = ids; break

        if not anon_ids:
            print("\n❌ No anon IDs. Exiting."); return

        # ── Stats BEFORE ──────────────────────────────────────────────────────
        print("\n📊 Fetching stats before linking...")
        elo_b, played_b = await fetch_stats(session, cookies)
        if elo_b is not None or played_b is not None:
            print(f"  ELO: {elo_b}  |  Challenges played: {played_b}")
        else:
            print("  (Could not fetch stats)")

        # ── Fire link requests ────────────────────────────────────────────────
        print(f"\n🚀 Firing {len(anon_ids)} link request(s) simultaneously...")
        t0      = time.perf_counter()
        results = await asyncio.gather(*[
            send_link_request(session, cookies, anon_ids[i], i+1)
            for i in range(len(anon_ids))
        ])
        print(f"  Done in {time.perf_counter()-t0:.4f}s | Statuses: {results}")

        # ── Stats AFTER ───────────────────────────────────────────────────────
        await asyncio.sleep(1.5)
        print("\n📊 Fetching stats after linking...")
        elo_a, played_a = await fetch_stats(session, cookies)
        if elo_a is not None or played_a is not None:
            print(f"  ELO: {elo_a}  |  Challenges played: {played_a}")
            print("\n✨ Changes:")
            if elo_b is not None and elo_a is not None:
                d = elo_a - elo_b
                print(f"  ELO score        : {elo_b} → {elo_a}  ({'+'if d>=0 else ''}{d})")
            if played_b is not None and played_a is not None:
                d = played_a - played_b
                print(f"  Challenges played: {played_b} → {played_a}  ({'+'if d>=0 else ''}{d})")
        else:
            print("  (Could not fetch stats)")

if __name__ == "__main__":
    asyncio.run(main())
