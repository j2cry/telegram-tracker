import argparse
import datetime as dt
import keyring
import pytz
from collections import defaultdict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, Defaults

from service import BotService, ContextSegment


if __name__ == '__main__':
    # parse initial cmd arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--schema', default='TRACKER', help='SQL schema')
    args = parser.parse_args()
    # collect credentials
    TOKEN = keyring.get_password('TRACKER_TELEGRAM', 'token')
    CONNSTR = keyring.get_password('TRACKER_DATABASE', 'connection')
    assert TOKEN is not None, f"Bot token is invalid"
    assert CONNSTR is not None, f"Database connection string is invalid"
    # initialize bot handlers
    bot = BotService(CONNSTR, schema=args.schema)
    TIMEZONE = dt.datetime.now().astimezone().tzinfo or pytz.UTC
    application = (Application.builder()
                    .token(TOKEN)
                    .read_timeout(bot.cf.READ_TIMEOUT)
                    .write_timeout(bot.cf.WRITE_TIMEOUT)
                    .connect_timeout(bot.cf.CONNECT_TIMEOUT)
                    .pool_timeout(bot.cf.POOL_TIMEOUT)
                    .defaults(Defaults(tzinfo=TIMEZONE))
                    .build())
    assert application.job_queue is not None, f"Cannot initialize job queue"
    # add command handlers
    application.add_handler(CommandHandler('start', bot.start))
    application.add_handler(CommandHandler('version', bot.version))
    application.add_handler(CommandHandler('actualize', bot.actualize))
    application.add_handler(CommandHandler('reload', bot.reload))
    application.add_handler(CommandHandler('subscript', bot.subscript))
    application.add_handler(CommandHandler('silent', bot.silent))
    application.add_handler(CommandHandler('check', bot.check))
    application.add_handler(CommandHandler('state', bot.state))
    application.add_handler(CommandHandler('shutdown', bot.shutdown))
    # add inline callbacks
    application.add_handler(CallbackQueryHandler(bot.access_response, 'access'))
    application.add_handler(CallbackQueryHandler(bot.subscript, 'subscript'))
    # error handler
    application.add_error_handler(bot._onerror)
    # add debug commands
    application.add_handler(CommandHandler("fox", bot.fox))
    application.add_handler(CommandHandler("debug", bot.debug))
    # setup required bot context
    application.bot_data[ContextSegment.ACCESS_REQUEST] = defaultdict(list)
    # run
    application.job_queue.run_once(bot._onstart, when=bot.cf.UPDOWN_DELAY)
    application.run_polling()
    bot.close()
