import asyncio
import contextlib
from collections import OrderedDict
from datetime import datetime
import httpx
from telegram import Update
from telegram.ext import ContextTypes
import db
from config import OWNER_CHAT_ID, AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL, AI_SYSTEM_PROMPT, CLAUDE_RUNNER_URL, CLAUDE_RUNNER_SECRET, CLAUDE_RUNNER_TIMEOUT

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


# --- optional Claude runner (enabled only when CLAUDE_RUNNER_URL is set) ---
RUNNER_ENABLED = bool(CLAUDE_RUNNER_URL and CLAUDE_RUNNER_SECRET)
_THREAD_ROOTS: "OrderedDict[tuple, int]" = OrderedDict()  # (chat_id, message_id) -> root message id
_ROOTS_MAX = 5000


def _remember_root(chat_id: int, message_id: int, root: int):
    _THREAD_ROOTS[(chat_id, message_id)] = root
    _THREAD_ROOTS.move_to_end((chat_id, message_id))
    while len(_THREAD_ROOTS) > _ROOTS_MAX:
        _THREAD_ROOTS.popitem(last=False)


def _thread_root(message) -> int:
    """Root message id of the reply chain this message belongs to."""
    chat_id = message.chat_id
    parent = message.reply_to_message
    if parent is None:
        root = message.message_id
    else:
        root = _THREAD_ROOTS.get((chat_id, parent.message_id))
        if root is None:
            grand = parent.reply_to_message
            root = grand.message_id if grand is not None else parent.message_id
    _remember_root(chat_id, message.message_id, root)
    return root


def _split(text: str, limit: int = 4096):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text or not parts:
        parts.append(text)
    return parts


async def _runner_post(path: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=CLAUDE_RUNNER_TIMEOUT) as client:
        return await client.post(
            CLAUDE_RUNNER_URL + path, json=payload,
            headers={"X-Runner-Secret": CLAUDE_RUNNER_SECRET},
        )


async def _typing(chat, thread_id):
    while True:
        with contextlib.suppress(Exception):
            await chat.send_action("typing", message_thread_id=thread_id)
        await asyncio.sleep(4)


async def _runner_answer(update: Update, text: str) -> str:
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    payload = {
        "chat_id": chat.id,
        "thread_root_id": _thread_root(msg),
        "user": user.full_name or user.username or str(user.id),
        "chat_title": chat.title or chat.full_name or str(chat.id),
        "question": text,
    }
    typing = asyncio.create_task(_typing(chat, msg.message_thread_id if chat.is_forum else None))
    try:
        r = await _runner_post("/ask", payload)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200 and body.get("answer"):
            return body["answer"]
        return "Error: " + str(body.get("error") or f"runner returned HTTP {r.status_code}")
    except httpx.TimeoutException:
        return "Error: the runner did not answer in time"
    except Exception as exc:
        return f"Error: runner unreachable ({type(exc).__name__})"
    finally:
        typing.cancel()


async def _reply_split(update: Update, text: str):
    for part in _split(text):
        sent = await update.message.reply_text(part)
        _remember_root(update.effective_chat.id, sent.message_id,
                       _THREAD_ROOTS.get((update.effective_chat.id, update.message.message_id), update.message.message_id))


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
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not RUNNER_ENABLED:
        return
    chat = update.effective_chat
    payload = {"chat_id": chat.id}
    msg = update.message
    if chat.type != "private" and msg.reply_to_message is not None:
        payload["thread_root_id"] = _thread_root(msg)
    try:
        r = await _runner_post("/reset", payload)
        r.raise_for_status()
    except Exception as exc:
        await msg.reply_text(f"Error: could not reset ({type(exc).__name__})")
        return
    if chat.type != "private" and "thread_root_id" not in payload:
        await msg.reply_text("Context cleared for all threads in this chat. (Reply with /new to a thread to clear only that one.)")
    else:
        await msg.reply_text("Context cleared. Starting a new conversation.")


@admin_or_owner
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = update.message.text or ""

    if chat.type != "private":
        mention = f"@{context.bot.username}"
        if mention not in text:
            return

    if RUNNER_ENABLED:
        await _reply_split(update, await _runner_answer(update, text))
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
