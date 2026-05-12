from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
import db
from config import OWNER_CHAT_ID, AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL, AI_SYSTEM_PROMPT

if AI_ENABLED and AI_API_KEY:
    from openai import AsyncOpenAI
    _ai_client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
else:
    _ai_client = None


async def _ai_reply(text: str) -> str:
    resp = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    return resp.choices[0].message.content


def admin_or_owner(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        if chat.type == "private":
            if user.id != OWNER_CHAT_ID:
                return
        else:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                return
            # Per-chat command allowlist (None = all allowed)
            allowed = db.get_chat_commands(chat.id)
            if allowed is not None:
                cmd_name = func.__name__.replace("cmd_", "")
                if cmd_name not in allowed:
                    return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


@admin_or_owner
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Personal assistant online.\n\n"
        "/todo <task>       — add a to-do\n"
        "/list              — show open to-dos\n"
        "/done <id>         — mark to-do done\n"
        "/del <id>          — delete a to-do\n"
        "/note <text>       — save a note\n"
        "/important <text>  — save an important note\n"
        "/pinned            — show important notes\n"
        "/summary           — send summary now\n"
    )


@admin_or_owner
async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /todo <task>")
        return
    tid = db.add_todo(text)
    await update.message.reply_text(f"Added todo #{tid}: {text}")


@admin_or_owner
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    todos = db.list_todos()
    if not todos:
        await update.message.reply_text("No open to-dos.")
        return
    lines = [f"#{t['id']} {t['text']}" for t in todos]
    await update.message.reply_text("Open to-dos:\n" + "\n".join(lines))


@admin_or_owner
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /done <id>")
        return
    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number.")
        return
    if db.mark_done(tid):
        await update.message.reply_text(f"Todo #{tid} marked done.")
    else:
        await update.message.reply_text(f"Todo #{tid} not found or already done.")


@admin_or_owner
async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /del <id>")
        return
    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number.")
        return
    if db.delete_todo(tid):
        await update.message.reply_text(f"Todo #{tid} deleted.")
    else:
        await update.message.reply_text(f"Todo #{tid} not found.")


@admin_or_owner
async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /note <text>")
        return
    nid = db.add_note(text, important=False)
    await update.message.reply_text(f"Note #{nid} saved.")


@admin_or_owner
async def cmd_important(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /important <text>")
        return
    nid = db.add_note(text, important=True)
    await update.message.reply_text(f"Important note #{nid} saved.")


@admin_or_owner
async def cmd_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = db.get_important_notes()
    if not notes:
        await update.message.reply_text("No important notes yet.")
        return
    lines = [f"#{n['id']} {n['text']}" for n in notes]
    await update.message.reply_text("Important notes:\n" + "\n".join(lines))


@admin_or_owner
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_summary(context.bot, update.effective_chat.id, force=True)


@admin_or_owner
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = update.message.text or ""

    if chat.type != "private":
        mention = f"@{context.bot.username}"
        if mention not in text:
            return

    if _ai_client:
        reply = await _ai_reply(text)
    else:
        reply = "Hi! I'm a bot — smarter replies are coming soon. Stay tuned!\nUse /note <text> to save a note."
    await update.message.reply_text(reply)


async def send_summary(bot, chat_id: int, force: bool = False):
    if force:
        # Manual /summary — always show everything
        todos = db.list_todos()
        notes = db.get_important_notes(5)
    else:
        # Scheduled — only items created since last summary was sent
        since = db.get_config("last_summary_sent")
        if not since:
            # First run — set baseline and skip; nothing to report yet
            db.set_config("last_summary_sent", datetime.utcnow().isoformat())
            return
        todos = db.list_todos_since(since)
        notes = db.get_important_notes_since(since)

    if not force and not todos and not notes:
        return

    parts = ["Summary"]

    if todos:
        parts.append(f"\nOpen to-dos ({len(todos)}):")
        parts.extend(f"  #{t['id']} {t['text']}" for t in todos)
    elif force:
        parts.append("\nNo open to-dos.")

    if notes:
        parts.append("\nTop important notes:")
        parts.extend(f"  #{n['id']} {n['text']}" for n in notes)
    elif force:
        parts.append("\nNo important notes.")

    await bot.send_message(chat_id=chat_id, text="\n".join(parts))

    if not force:
        db.set_config("last_summary_sent", datetime.utcnow().isoformat())
