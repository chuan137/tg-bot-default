import logging
from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import db
import handlers
from config import TELEGRAM_TOKEN, OWNER_CHAT_ID, SUMMARY_INTERVAL_MINUTES

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


_ALL_HANDLERS = {
    "todo":      (handlers.cmd_todo,      "Add a to-do: /todo <task>"),
    "list":      (handlers.cmd_list,      "Show open to-dos"),
    "done":      (handlers.cmd_done,      "Mark done: /done <id>"),
    "del":       (handlers.cmd_del,       "Delete: /del <id>"),
    "note":      (handlers.cmd_note,      "Save a note"),
    "important": (handlers.cmd_important, "Save an important note"),
    "pinned":    (handlers.cmd_pinned,    "Show important notes"),
    "summary":   (handlers.cmd_summary,   "Send summary now"),
}


async def post_init(app):
    cmds = [BotCommand(c, desc) for c, (_, desc) in _ALL_HANDLERS.items()]
    for scope in (BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats()):
        await app.bot.set_my_commands(cmds, scope=scope)
    logger.info("Registered commands: %s", [c.command for c in cmds])


async def scheduled_summary(context):
    await handlers.send_summary(context.bot, OWNER_CHAT_ID)


def main():
    db.init_db()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    async def _track(update, context):
        if update.effective_chat:
            c = update.effective_chat
            if c.type == "private" and (not update.effective_user or update.effective_user.id != OWNER_CHAT_ID):
                return
            db.track_chat(c.id, c.title or c.full_name or str(c.id), c.type)

    app.add_handler(MessageHandler(filters.ALL, _track), group=-1)
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    for cmd, (handler, _) in _ALL_HANDLERS.items():
        app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_message)
    )

    app.job_queue.run_repeating(
        scheduled_summary,
        interval=SUMMARY_INTERVAL_MINUTES * 60,
        first=SUMMARY_INTERVAL_MINUTES * 60,
    )

    logger.info("Bot starting, summary every %d min", SUMMARY_INTERVAL_MINUTES)
    app.run_polling()


if __name__ == "__main__":
    main()
