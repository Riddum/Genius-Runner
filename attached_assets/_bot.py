"""
Telegram bot interface for the India Genius Challenge automation.

Commands:
  /start   — show help
  /status  — today's cache count
  /collect [n]         — run n probes to refresh answer bank (default 50)
  /generate [n] [speed]— generate n perfect anon IDs  (speed 1=<30s, 2=<50s)
  /verify <id>         — check score of any anon attempt ID via server
"""
import asyncio
import os
import sys
import time

# Run from this script's directory so relative paths (cache files) work
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode

from genius_1780164377809 import (
    load_cache, save_cache, generate_attempt, validate_answer,
    run_probe_attempt, collect_answers, merged_quiz_cache,
    QUIZ_KEY, BASE_URL, HEADERS, aiohttp, random
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

SPEED_PROFILES = {
    "1": {"label": "⚡ Under 30s (~22s avg)", "lo": 0.8,  "hi": 1.5},
    "2": {"label": "🐢 Under 50s (~38s avg)", "lo": 2.0,  "hi": 3.2},
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def cache_status():
    cache = load_cache()
    count = len(cache.get(QUIZ_KEY, {}))
    return count, QUIZ_KEY

async def verify_attempt(session, anon_id: str, retries: int = 4):
    """
    Fetch attempt result from GET /api/attempt/:id.
    Retries with backoff on 429 rate-limit. Returns None on persistent failure.
    """
    for attempt in range(retries):
        try:
            async with session.get(
                f"{BASE_URL}/attempt/{anon_id}",
                headers=HEADERS,
                cookies={"anon_attempt_id": anon_id},
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                data = await resp.json(content_type=None)
            if not data or not data.get("success"):
                await asyncio.sleep(3)
                continue
            attempted  = data.get("attemptedQuestions", [])
            total      = data.get("totalQuestions", 15)
            unanswered = sum(1 for e in attempted if not e.get("selectedOption"))
            t_total    = data.get("attemptData", {}).get("timeTakenTotal", 0)
            return {
                "score":      data.get("score", 0),
                "total":      total,
                "correct":    sum(1 for e in attempted if e.get("isCorrect")),
                "unanswered": unanswered,
                "time":       round(t_total, 1),
            }
        except Exception:
            await asyncio.sleep(3)
    return None


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

# ─── Command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count, key = cache_status()
    await update.message.reply_text(
        f"*India Genius Challenge Bot* 🎯\n\n"
        f"Answer bank today: *{count} questions* cached (`{key}`)\n\n"
        f"*Commands:*\n"
        f"`/status` — show cache count\n"
        f"`/collect [n]` — refresh answer bank (default 50 probes)\n"
        f"`/generate [n] [speed]` — create perfect anon IDs\n"
        f"  • speed `1` = under 30s (default)\n"
        f"  • speed `2` = under 50s\n\n"
        f"_Example:_ `/generate 5 1`",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count, key = cache_status()
    await update.message.reply_text(
        f"📊 *Answer bank status*\n\n"
        f"Quiz key: `{key}`\n"
        f"Cached answers: *{count}*",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    n = 50
    if args and args[0].isdigit():
        n = max(1, min(500, int(args[0])))

    count, _ = cache_status()
    msg = await update.message.reply_text(
        f"🔍 *Collecting answers* — {n} probes at concurrency 4\n"
        f"Starting with {count} cached...\n\n"
        f"⏳ Progress: 0/{n}",
        parse_mode=ParseMode.MARKDOWN
    )

    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        cache         = load_cache()
        quiz_cache    = merged_quiz_cache(cache)    # cross-day merge
        question_meta = dict(cache.get("question_meta", {}))
        tried_options = {}
        sem           = asyncio.Semaphore(4)

        completed = 0
        last_edit = time.time()

        tasks = [
            run_probe_attempt(session, quiz_cache, question_meta, tried_options, sem, i)
            for i in range(n)
        ]

        for coro in asyncio.as_completed(tasks):
            await coro
            completed += 1
            now = time.time()
            # Edit message every 10 probes or every 15 seconds
            if completed % 10 == 0 or completed == n or (now - last_edit) > 15:
                pct  = completed * 100 // n
                bars = "█" * (pct // 5) + "░" * (20 - pct // 5)
                try:
                    await msg.edit_text(
                        f"🔍 *Collecting answers* — {n} probes\n"
                        f"[{bars}] {completed}/{n}\n"
                        f"Answers cached: *{len(quiz_cache)}*",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    last_edit = now
                except Exception:
                    pass

        # Save
        cache[QUIZ_KEY]        = quiz_cache
        cache["question_meta"] = question_meta
        save_cache(cache)

        await msg.edit_text(
            f"✅ *Collection complete!*\n\n"
            f"Probes run: {n}\n"
            f"Total answers cached: *{len(quiz_cache)}*\n\n"
            f"Use `/generate` to create perfect anon IDs.",
            parse_mode=ParseMode.MARKDOWN
        )

async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    # Parse n and speed from args
    n     = 3
    speed = "1"

    if len(args) >= 1 and args[0].isdigit():
        n = max(1, min(20, int(args[0])))
    if len(args) >= 2 and args[1] in SPEED_PROFILES:
        speed = args[1]

    # If no speed given, show inline keyboard
    if len(args) < 2:
        keyboard = [
            [
                InlineKeyboardButton("⚡ Under 30s", callback_data=f"gen:{n}:1"),
                InlineKeyboardButton("🐢 Under 50s", callback_data=f"gen:{n}:2"),
            ]
        ]
        await update.message.reply_text(
            f"Generate *{n}* perfect anon ID(s). Choose speed:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await _run_generate(update.message, n, speed)

async def callback_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, n_str, speed = query.data.split(":")
    n = int(n_str)
    await query.edit_message_text(
        f"Generating *{n}* ID(s) — {SPEED_PROFILES[speed]['label']}...",
        parse_mode=ParseMode.MARKDOWN
    )
    await _run_generate(query.message, n, speed, editing=True)

async def _run_generate(message, n: int, speed: str, editing=False):
    profile = SPEED_PROFILES.get(speed, SPEED_PROFILES["1"])
    lo, hi  = profile["lo"], profile["hi"]

    count, _ = cache_status()
    if count == 0:
        await message.reply_text(
            "❌ No answers cached. Run `/collect` first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    status_msg = await message.reply_text(
        f"🎯 *Generating {n} perfect anon ID(s)*\n"
        f"{profile['label']}\n\n"
        f"⏳ 0/{n} done...",
        parse_mode=ParseMode.MARKDOWN
    )

    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        cache         = load_cache()
        quiz_cache    = merged_quiz_cache(cache)    # cross-day merge
        question_meta = dict(cache.get("question_meta", {}))

        # Check cache coverage — no gap-fill (gap-fill before generation causes rate-limiting)
        _, ref_questions, _ = await generate_attempt(session)
        uncached_count = 0
        if ref_questions:
            uncached_count = sum(1 for q in ref_questions if q["_id"] not in quiz_cache)
            if uncached_count:
                await status_msg.edit_text(
                    f"🎯 *Generating {n} perfect anon ID(s)*\n"
                    f"{profile['label']}\n\n"
                    f"⚠️ *{uncached_count}/15 questions not in cache* — will use first option (may be wrong)\n"
                    f"💡 Run `/collect 200` first for guaranteed 15/15\n\n"
                    f"⏳ 0/{n} done...",
                    parse_mode=ParseMode.MARKDOWN
                )

        anon_ids  = []
        tries     = 0
        max_tries = n * 4

        while len(anon_ids) < n and tries < max_tries:
            tries += 1
            aid, elapsed, _, _ = await create_perfect_attempt(session, quiz_cache, lo, hi)
            if aid:
                # Verify via API (authoritative server score)
                await asyncio.sleep(1)
                v = await verify_attempt(session, aid)
                if v:
                    score = f"{v['correct']}/{v['total']}"
                    icon  = "✅" if v["correct"] == v["total"] else "⚠️"
                    detail = f"_{elapsed}s_ | server: {score} correct"
                else:
                    score  = "?"
                    icon   = "❓"
                    detail = f"_{elapsed}s_ | verify failed"
                anon_ids.append((aid, detail, score, icon))
                lines = "\n".join(
                    f"{ic} `{a}`\n   {d}"
                    for a, d, s, ic in anon_ids
                )
                try:
                    await status_msg.edit_text(
                        f"🎯 *Generating {n} anon ID(s)*\n"
                        f"{profile['label']}\n\n"
                        f"{len(anon_ids)}/{n} done\n\n"
                        f"{lines}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
            else:
                await asyncio.sleep(2)

        # Final message
        if not anon_ids:
            await status_msg.edit_text("❌ All attempts failed. Check server connectivity.")
            return

        all_perfect  = all(s == s.replace(s.split("/")[0], s.split("/")[1], 1)
                           if "/" in s else False for _, _, s, _ in anon_ids)
        all_perfect  = all(ic == "✅" for _, _, _, ic in anon_ids)
        ids_oneliner = " ".join(a for a, _, _, _ in anon_ids)
        lines        = "\n".join(
            f"{ic} `{a}`\n   {d}"
            for a, d, s, ic in anon_ids
        )
        header = "All perfect 15/15" if all_perfect else "Check scores below"

        await status_msg.edit_text(
            f"🎯 *{len(anon_ids)} anon ID(s) ready* — {header}\n\n"
            f"{lines}\n\n"
            f"*One-liner for [3] Manual link:*\n"
            f"`{ids_oneliner}`",
            parse_mode=ParseMode.MARKDOWN
        )

async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/verify <anon_attempt_id>`\n"
            "Example: `/verify 6a1b357c5dd10f7b6c2edf88`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    anon_id = args[0].strip()
    msg = await update.message.reply_text(f"🔍 Verifying `{anon_id}`...", parse_mode=ParseMode.MARKDOWN)

    connector = aiohttp.TCPConnector(limit=10, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        v = await verify_attempt(session, anon_id)

    if not v:
        await msg.edit_text(
            f"❌ Could not fetch results for `{anon_id}`\n"
            f"The ID may be invalid or expired.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    icon = "✅" if v["correct"] == v["total"] else "⚠️"
    lines = [
        f"{icon} *Score: {v['correct']}/{v['total']} correct*",
        f"⏱ Time: {v['time']}s",
    ]
    if v["unanswered"] > 0:
        lines.append(f"⚠️ Unanswered: {v['unanswered']} (server had empty submissions)")

    await msg.edit_text(
        f"📊 *Attempt Verification*\n"
        f"`{anon_id}`\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN
    )

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("collect",  cmd_collect))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("verify",   cmd_verify))
    app.add_handler(CallbackQueryHandler(callback_generate, pattern=r"^gen:"))
    print("Bot started — polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
