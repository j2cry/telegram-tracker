import os
import re
import signal
import argparse
import pymssql
import math
import json
import datetime as dt
import logging
from logging.handlers import RotatingFileHandler
from ast import literal_eval
from threading import Lock
from collections import defaultdict, namedtuple
from functools import wraps
from enum import Enum
from typing import Any, Mapping
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, Defaults, ContextTypes, CallbackContext, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from connectors import ConnectorMap, Connector


def __get_version__():
    MAIN_VERSION = '2.0'
    try:
        with open('.buildno', 'r') as file:
            build = file.read().strip()
    except:
        build = 'unknown'
    return f'{MAIN_VERSION}.{build}'


class Permission:
    BLOCKED = 0x00
    USER =    0x01
    ADMIN =   0x02
    MASTER =  0x04
    MODERATOR = ADMIN | MASTER
    REGISTRED = USER | ADMIN | MASTER


class DefaultConfig:
    REQUEST_MAXTIME = 43200 # 12 hours
    READ_TIMEOUT = 150      # 2.5 minutes
    CHANNELS_PER_PAGE = 5
    DELAY = 2.5
    SUBSCRIPTIONS_MENU_HEADER = 'Currently available channels ({page}/{total})'
    TEXT_MAX_LENGTH = 4096
    ACTUALIZE_INTERVAL = 900
    POLLING = 600
    SUSPEND_SUBSCRIPTION = 'Channel {name} was disabled. Your subscription has been suspended.'
    RESUME_SUBSCRIPTION = 'Channel {name} was enabled. Your subscription has been renewed.'


class CheckMark(Enum):
    TRUE = '\u2714'
    FALSE = '\u2716'


class BotService:
    """ Class for handling bot service interactions """
    def __init__(self, logger, **params):
        self.__lock = Lock()
        self.__conn = pymssql.connect(server=params.get('server'),
                                      database=params.get('database'),
                                      user=params.get('user'),
                                      password=params.get('password'),
                                      as_dict=True, autocommit=True)
        self.__cursor = self.__conn.cursor()
        self.logger = logger

    def close(self) -> None:
        self.__cursor.close()
        self.__conn.close()

    @staticmethod
    def parse_jobtime(value: str, **kwargs):
        """ Job time parser. Returns parsed value of first applicable format or default value. If default is not specifed raises ValueError

        Parameters
        ----------
        value : str
            Original parameter value
        default : Any
            Default job time value

        Return
        ------
        float or time
        """
        formatter = (float, dt.time.fromisoformat)
        for func in formatter:
            try:
                return func(value)
            except:
                ...
        if 'default' in kwargs:
            return kwargs['default']
        else:
            raise ValueError('None of the known formats are applicable')

    def get_parameter(self, key: str, recast=lambda v: v, default=None) -> Any:
        """ Get global parameter or default if parameter not exists

        Parameters
        ----------
        key : str
            Parameter identifier
        recast : callable
            Handler function, i.e. type recast; by default returns original value

        Return
        ------
        Any : value of any type
        """
        with self.__lock:
            self.__cursor.execute('SELECT argument FROM TRACKER.parameter WHERE identifier = %s', params=(key,))
            result = self.__cursor.fetchone()
        try:
            return recast(result['argument'])
        except:
            return result if result is not None else default

    def get_permission_flag(self, uid: str|int) -> bool|None:
        """ Get user access level

        Parameters
        ----------
        uid : str or int
            Telegram user id

        Return
        ------
        bool or None if no such user found
        """
        with self.__lock:
            self.__cursor.execute('SELECT flag FROM TRACKER.permission WHERE user_id = %s', params=(uid,))
            result = self.__cursor.fetchone()
        return result['flag'] if result else None

    def set_permission_flag(self, uid: str|int, flag: int = Permission.USER) -> None:
        """ Set user access level

        Parameters
        ----------
        uid : str or int
            Telegram user id
        flag : int
            Permission flag
        """
        with self.__lock:
            query = """
                IF EXISTS (SELECT flag FROM TRACKER.permission WHERE user_id = %s)
                    UPDATE TRACKER.permission
                    SET flag = %s
                    WHERE user_id = %s
                ELSE
                    INSERT INTO TRACKER.permission VALUES (%s, %s)
            """
            self.__cursor.execute(query, params=(uid, flag, uid, uid, flag))

    def get_active_channels(self) -> tuple:
        """ Get active channels configuration

        Return
        ------
        tuple of dict
        """
        with self.__lock:
            self.__cursor.execute('SELECT * FROM TRACKER.channel WHERE active = 1')
            return tuple(self.__cursor.fetchall())

    def get_subscriptions(self, uid: str|int) -> tuple:
        """ Get channel ids of actual user subscriptions

        Parameters
        ----------
        uid : str or int
            Telegram user id

        Return
        ------
        tuple of int
        """
        with self.__lock:
            self.__cursor.execute('SELECT channel_id FROM TRACKER.subscription WHERE user_id = %s AND active = 1', params=(uid,))
            return tuple(item['channel_id'] for item in self.__cursor.fetchall())

    def set_subscription(self, uid: str|int, cid: str|int, active: bool) -> None:
        """ Add or update user subscription

        Parameters
        ----------
        uid : str or int
            Telegram user id
        cid : str or int
            Channel id
        active : bool
            New subscription status
        """
        with self.__lock:
            query = """
                IF EXISTS (SELECT active FROM TRACKER.subscription WHERE user_id = %s AND channel_id = %s)
                    UPDATE TRACKER.subscription
                    SET active = %s
                    WHERE user_id = %s AND channel_id = %s
                ELSE
                    INSERT INTO TRACKER.subscription VALUES (%s, %s, %s)
            """
            self.__cursor.execute(query, params=(uid, cid, active, uid, cid, uid, cid, active))

    def get_subscribers(self, channel_id: str|int) -> tuple:
        """ Get ids of users subscribed for given channel

        Parameters
        ----------
        channel_id : str or int
            Channel id

        Return
        ------
        tuple of int
        """
        with self.__lock:
            self.__cursor.execute('SELECT user_id FROM TRACKER.subscription WHERE channel_id = %s AND active = 1', params=(channel_id,))
            return tuple(item['user_id'] for item in self.__cursor.fetchall())

    def default_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, *, optional: dict = None):
        """ Get default answer and reply """
        answer = self.get_parameter(key, default='BROKEN MESSAGE. Check your tracker installation.')
        return update.message.reply_text(answer.format(**optional if optional and isinstance(optional, Mapping) else {}))

    def logcommand(method):
        """ Log command decorator """
        @wraps(method)
        def _wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            user = update.effective_user
            with_args = f" with args `{' '.join(context.args)}`" if context.args else ''
            username = f" [{user.username}]" if user.username else ''
            self.logger.info(f'[COMMAND] Command `{method.__name__}`{with_args} was accepted for {update.effective_user.id}{username}')
            return method(self, update, context)
        return _wrapper

    def access(permissions: int = Permission.MASTER):
        """ Command access level decorator """
        def _decorator(method):
            @wraps(method)
            def _wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                user = update.effective_user
                # request and compare user permissions
                if (flag := self.get_permission_flag(user.id)) is None:
                    return self.start(update, context)
                elif permissions & flag:
                    return method(self, update, context)
                else:
                    username = f" [{user.username}]" if user.username else ''
                    self.logger.info(f'[COMMAND] Command `{method.__name__}` was rejected for {update.effective_user.id}{username}')
                    return self.default_reply(update, context, 'NOT_ALLOWED', optional={'flag': flag})
            return _wrapper
        return _decorator

    async def _actualize(self, context: CallbackContext) -> None:
        """ Syncronize listener jobs with actual channels list """
        channels = self.get_active_channels()
        # ids for active and current running channels
        active = {item['channel_id']: item['identifier'] for item in channels}
        current = {}
        modified = {}
        for job in context.job_queue.jobs():
            if not job.name.startswith('listener'):
                continue
            current[job.data.cid] = job.data.name
            modified[job.data.cid] = job.data.context
            job.data.close()
            job.schedule_removal()
        # update connectors
        for channel in channels:
            # parse connector parameters
            connectorClass = ConnectorMap[channel['connector'].upper()].value
            config = json.loads(channel['config'])
            connector = connectorClass(channel['channel_id'], channel['identifier'], logger=self.logger, **config, **modified.get(channel['channel_id'], {}))
            # create listener job
            jobtime = self.parse_jobtime(channel['polling'], default=DefaultConfig.POLLING)
            if isinstance(jobtime, dt.time):
                job = context.job_queue.run_daily(self._listen, time=jobtime, name=f'listener{connector.cid}', data=connector)
            else:
                job = context.job_queue.run_repeating(self._listen, interval=jobtime, name=f'listener{connector.cid}', data=connector)
            self.logger.info(f'Listener job for {job.data.name} scheduled at {job.next_t}')
        # send notifications
        if not self.get_parameter('SILENT_ACTUALIZE', literal_eval, default=False):
            NOTIFICATION = self.get_parameter('RESUME_SUBSCRIPTION', default=DefaultConfig.RESUME_SUBSCRIPTION)
            for cid in set(active).difference(current):
                for subscriber in self.get_subscribers(cid):
                    await context.bot.send_message(subscriber, NOTIFICATION.format(name=active[cid]))
            NOTIFICATION = self.get_parameter('SUSPEND_SUBSCRIPTION', default=DefaultConfig.SUSPEND_SUBSCRIPTION)
            for cid in set(current).difference(active):
                for subscriber in self.get_subscribers(cid):
                    await context.bot.send_message(subscriber, NOTIFICATION.format(name=current[cid]))
        # reschedule actualizer job
        ACTUALIZE_INTERVAL = self.get_parameter('ACTUALIZE_INTERVAL', self.parse_jobtime, default=DefaultConfig.ACTUALIZE_INTERVAL)
        if isinstance(ACTUALIZE_INTERVAL, dt.time):
            ACTUALIZE_INTERVAL = ACTUALIZE_INTERVAL.replace(tzinfo=context.job_queue.scheduler.timezone)
        if isinstance(ACTUALIZE_INTERVAL, dt.time) or ACTUALIZE_INTERVAL > 0:
            for job in context.job_queue.get_jobs_by_name('actualize'):
                job.schedule_removal()
            job = context.job_queue.run_once(self._actualize, when=ACTUALIZE_INTERVAL, name='actualize')
            self.logger.info(f'Actualizer job scheduled at {job.next_t}.')
        self.logger.info('[SYSTEM] Channels actualized')

    async def _listen(self, context: CallbackContext) -> None:
        """ Send notifications """
        self.logger.info(f'Listener job for {context.job.data.name} scheduled at {context.job.next_t}')
        connector: Connector = context.job.data
        content = connector.check()    # get channel updates
        if not content:
            self.logger.info(f'Channel {connector.cid} ({connector.name}) has no updates.')
            return
        TEXT_MAX_LENGTH = self.get_parameter('TEXT_MAX_LENGTH', int, default=DefaultConfig.TEXT_MAX_LENGTH)
        for subscriber in self.get_subscribers(connector.cid):
            sleep_time = context._application.user_data[subscriber].get('silent')
            if sleep_time and sleep_time > dt.datetime.now():
                self.logger.info(f'[SKIP] Notifications for subscriber {subscriber} are disabled until {sleep_time}')
                continue
            # split text to Telegram messages
            for text in content:
                text = f'[{connector.last_modified.replace(microsecond=0)}] {connector.name}:\n{text}'
                for bound in range(0, len(text), TEXT_MAX_LENGTH):
                    _message_part = text[bound:bound + TEXT_MAX_LENGTH]
                    try:
                        await context.bot.send_message(subscriber, _message_part)
                    except Exception as ex:
                        self.logger.error(f'[NOT SENT] {subscriber} ' + _message_part.replace('\n', ' '))
                    else:
                        self.logger.info(f'[SENT] {subscriber} ' + _message_part.replace('\n', ' '))
                # message proceeded
        self.logger.info(f'Channel {connector.cid} ({connector.name}) update succeed.')

    async def _silent_off(self, context: CallbackContext) -> None:
        """ Silent mode was ended notification """
        context.user_data['silent'] = None
        if TEXT := self.get_parameter('NOTIFICATION_ENABLED'):
            await context.bot.send_message(context._user_id, TEXT)

    async def _access_autoreject(self, context: CallbackContext) -> None:
        """ Reject access request after timeout """
        messages = context.bot_data['access_request'][context._user_id]
        answer = self.get_parameter(f'REQUEST_REJECTED').format(username=context.bot.name)
        for message in messages:
            await message.edit_text(answer)
        context.bot_data['access_request'][context._user_id] = []
        await context.bot.send_message(context._user_id, answer)

    async def _onstart(self, context: CallbackContext) -> None:
        """ Start log listeners """
        await self._actualize(context)

    async def _onclose(self, context: CallbackContext) -> None:
        """ Stop log listeners and kill self """
        context.job_queue.scheduler.remove_all_jobs()   # cancel all jobs
        self.logger.info('All jobs have been removed.')
        os.kill(os.getpid(), signal.SIGTERM)
        self.logger.info('SIGTERM was sent.')

    async def _error(self, update: object, context: CallbackContext):
        """ Error handler """
        self.logger.info(f'[ERROR] {context.error}')

    @logcommand
    async def version(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Send current tracker version """
        await update.message.reply_text(__get_version__())

    @logcommand
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Start bot interactions with sending access request """
        if (flag := self.get_permission_flag(update.effective_user.id)) is not None:
            return await self.default_reply(update, context, 'ALREADY_ACCESSIBLE', optional={'flag': flag})
        user = update.effective_user
        messages = context.bot_data['access_request'][user.id]
        REQUEST_MAXTIME = self.get_parameter('REQUEST_MAXTIME', float, default=DefaultConfig.REQUEST_MAXTIME)
        if not messages:
            with self.__lock:   # collect admins list
                self.__cursor.execute('SELECT user_id FROM TRACKER.permission WHERE flag >= %s', params=(Permission.ADMIN,))
                admins = tuple(item['user_id'] for item in self.__cursor.fetchall())
            if not admins:      # if there is no admins yet
                self.set_permission_flag(user.id, Permission.MASTER)
                return await self.default_reply(update, context, 'REQUEST_APPROVED', optional={'username': context.bot.name})
            # prepare inline
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(CheckMark['FALSE'].value, callback_data=f'access,{user.id},REJECTED'),
                InlineKeyboardButton(CheckMark['TRUE'].value, callback_data=f'access,{user.id},APPROVED')
            ]])
            # send notifications to all admins
            maxtime = dt.datetime.now().replace(microsecond=0) + dt.timedelta(seconds=REQUEST_MAXTIME)
            request_text = self.get_parameter('ACCESS_REQUEST_MESSAGE').format(username=user.username if user.username else user.id, maxtime=maxtime)
            for admin in admins:
                messages.append(await context.bot.send_message(admin, request_text, reply_markup=markup))
            context.job_queue.run_once(self._access_autoreject, maxtime, name=f'access{user.id}', user_id=user.id)
        else:
            maxtime = context.job_queue.get_jobs_by_name(f'access{user.id}')[0].next_t
        await self.default_reply(update, context, 'ACCESS_REQUESTED', optional={'maxtime': maxtime})

    @access(Permission.MASTER | Permission.ADMIN)
    @logcommand
    async def access_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Send access response """
        await update.callback_query.answer()
        admin = update.effective_user
        AccessRequestCallback = namedtuple('AccessCallback', 'uid,decision')
        data = AccessRequestCallback(*update.callback_query.data.split(',')[1:])
        if data.decision == 'APPROVED':
            self.set_permission_flag(data.uid, Permission.USER)
        # send messages
        messages = context.bot_data['access_request'][int(data.uid)]
        answer = self.get_parameter(f'REQUEST_{data.decision}').format(username=admin.name if admin.name else admin.id)
        for message in messages:
            await message.edit_text(answer)
        await context.bot.send_message(data.uid, answer)
        # clean context
        context.bot_data['access_request'][int(data.uid)] = []
        for job in context.job_queue.get_jobs_by_name(f'access{data.uid}'):
            job.schedule_removal()

    @access(Permission.MASTER | Permission.ADMIN)
    @logcommand
    async def actualize(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Syncronize listener jobs with actual channels list """
        await self._actualize(context)
        await self.default_reply(update, context, 'ACTUALIZE_REPLY')

    @access(Permission.MASTER)
    @logcommand
    async def shutdown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Shutdown Tracker """
        DELAY = self.get_parameter('DELAY', float, default=DefaultConfig.DELAY)
        await self.default_reply(update, context, 'SHUTDOWN_REPLY')
        context.job_queue.run_once(self._onclose, when=DELAY)

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def subscript(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Configure individual subscriptions """
        user = update.effective_user
        message = context.user_data.get('subscription')
        # parse target page
        SubscriptionCallback = namedtuple('SubscriptionCallback', 'page,cid')
        data = SubscriptionCallback(*update.callback_query.data.split(',')[1:] if update.callback_query else ('0', 'None'))
        try:
            page = int(data.page)
        except ValueError:
            if message is not None:
                await message.delete()
                context.user_data['subscription'] = None
            return
        # parse channel id and update subscription
        subscriptions = self.get_subscriptions(user.id)
        if (cid := literal_eval(data.cid)) is not None:
            self.set_subscription(user.id, cid, cid not in subscriptions)
            subscriptions = self.get_subscriptions(user.id)
        # calculate current page
        channels = self.get_active_channels()
        try:
            PER_PAGE = self.get_parameter('CHANNELS_PER_PAGE', int, default=DefaultConfig.CHANNELS_PER_PAGE)
            if PER_PAGE < 1:
                raise ValueError()
        except:
            PER_PAGE = DefaultConfig.CHANNELS_PER_PAGE
        maxpage = math.ceil(len(channels) / PER_PAGE) - 1
        if page < 0:
            page = maxpage
        elif page > maxpage:
            page = 0
        # prepare and show subscriptions menu
        markup = InlineKeyboardMarkup([
            *[[InlineKeyboardButton(f'{CheckMark[str(ch["channel_id"] in subscriptions).upper()].value} {ch["identifier"]}', callback_data=f'subscript,{page},{ch["channel_id"]}')] for ch in channels[PER_PAGE * page:PER_PAGE * (page + 1)]],
            [InlineKeyboardButton('<<', callback_data=f'subscript,{page - 1},None'), InlineKeyboardButton('>>', callback_data=f'subscript,{page + 1},None')],
            [InlineKeyboardButton('OK', callback_data='subscript,ok,None')]
        ])
        TEXT = self.get_parameter('SUBSCRIPTIONS_MENU_HEADER', default=DefaultConfig.SUBSCRIPTIONS_MENU_HEADER).format(page=page + 1, total=maxpage + 1)
        if message is None:
            context.user_data['subscription'] = await context.bot.send_message(user.id, TEXT, reply_markup=markup)
        else:
            await update.callback_query.answer()
            if maxpage or cid is not None:
                await message.edit_text(TEXT, reply_markup=markup)

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def silent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Silent all notifications """
        def get_delta_params(source):
            """ Timedelta argument parser """
            class KeyMapper(Enum):
                d = 'days'
                h = 'hours'
                m = 'minutes'
                s = 'seconds'
            return {KeyMapper[item[1]].value: int(item[0]) for item in re.findall(rf'(\d+)({"|".join(KeyMapper._member_names_)})', source)}

        formatter = [   # argument parsers used
            dt.datetime.fromisoformat,
            lambda arg: dt.datetime.fromisoformat(f'{dt.date.today()} {dt.time.fromisoformat(arg)}'),
            lambda arg: dt.datetime.now().replace(microsecond=0) + dt.timedelta(**get_delta_params(arg))
        ]
        timearg = ' '.join(context.args)
        for recast in formatter:
            try:
                sleep_time = recast(timearg)
                break
            except:
                sleep_time = None
        if sleep_time is None or sleep_time <= dt.datetime.now():
            return await self.default_reply(update, context, 'WRONG_ARGUMENT')
        # setup context
        context.user_data['silent'] = sleep_time
        await self.default_reply(update, context, 'NOTIFICATION_DISABLED', optional={'sleeptime': sleep_time})
        delta = sleep_time - dt.datetime.now()
        context.job_queue.run_once(self._silent_off, when=delta, user_id=update.effective_user.id)

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Check for channel updates immediately without affecting on scheduled jobs """
        message = await self.default_reply(update, context, 'CHECK_REPLY')
        for job in context.job_queue.jobs():
            if job.name.startswith('listener'):
                await job.run(context.application)
        await message.reply_text('done')

    @access(Permission.MASTER)
    async def state(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Return current Tracker state """
        # collect scheduled jobs
        _state = f'[{dt.datetime.now().replace(microsecond=0)}] SELF STATE:\n'
        for job in context.job_queue.jobs():
            _state += f'{getattr(job.data, "name", job.name)} @ {job.next_t.replace(microsecond=0, tzinfo=None)}\n'
        await context.bot.send_message(update.effective_user.id, _state)

    @access(Permission.MASTER)
    async def debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """ Debug feature """
        ...

    # ACCESS DEBUG
    @access(Permission.MASTER)
    async def master(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text('Hello, Master')

    @access(Permission.ADMIN)
    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text('Hello, Admin')

    @access(Permission.USER)
    async def user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text('Hello, User')

    async def anyone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text('Hello, Guest')

    @access(Permission.BLOCKED)
    async def blocked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text('!@#$%^')


if __name__ == '__main__':
    # parse initial cmd arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--user', help='SQL username')
    parser.add_argument('-p', '--password', help='SQL password')
    parser.add_argument('-s', '--server', help='SQL server')
    parser.add_argument('-d', '--database', help='SQL database')
    parser.add_argument('--logfile', default='logs/tracker.log', help='output logfile')
    parser.add_argument('--logsize', type=int, default=1048576, help='logfile max size (in bytes)')
    parser.add_argument('--logbackup', type=int, default=5, help='max number of log backups')
    parser.add_argument('--loglevel', default='DEBUG', help='logging level')
    args = parser.parse_args()

    # initialize logger
    if (dirname := os.path.dirname(args.logfile)) and not os.path.exists(dirname):
        os.makedirs(dirname)
    log_handler = RotatingFileHandler(
        args.logfile, mode='w',
        maxBytes=args.logsize,
        backupCount=args.logbackup,
        encoding='utf-8'
    )
    log_handler.setFormatter(logging.Formatter('[%(levelname)s] %(asctime)s > %(message)s'))
    log_handler.setLevel(args.loglevel)
    logger = logging.getLogger('TelegramTrackerService')
    logger.setLevel(args.loglevel)
    logger.addHandler(log_handler)
    logger.info('Logger initialized')

    # initialize bot handlers
    bot_service = BotService(logger, **args.__dict__)
    TOKEN = bot_service.get_parameter('TOKEN')
    READ_TIMEOUT = bot_service.get_parameter('READ_TIMEOUT', float, default=DefaultConfig.READ_TIMEOUT)
    # DefaultConfig = DefaultConfig()
    TIMEZONE = dt.datetime.now().astimezone().tzinfo
    application = Application.builder().token(TOKEN).read_timeout(READ_TIMEOUT).defaults(Defaults(tzinfo=TIMEZONE)).build()
    application.add_handler(CommandHandler('start', bot_service.start))
    application.add_handler(CommandHandler('version', bot_service.version))
    application.add_handler(CommandHandler('subscript', bot_service.subscript))
    application.add_handler(CommandHandler('silent', bot_service.silent))
    application.add_handler(CommandHandler('check', bot_service.check))
    application.add_handler(CommandHandler('actualize', bot_service.actualize))
    application.add_handler(CommandHandler('state', bot_service.state))
    application.add_handler(CommandHandler('shutdown', bot_service.shutdown))
    # inline callbacks
    application.add_handler(CallbackQueryHandler(bot_service.access_response, 'access'))
    application.add_handler(CallbackQueryHandler(bot_service.subscript, 'subscript'))
    # error handler
    application.add_error_handler(bot_service._error)
    # debug commands
    application.add_handler(CommandHandler("debug", bot_service.debug))
    application.add_handler(CommandHandler("master", bot_service.master))
    application.add_handler(CommandHandler("admin", bot_service.admin))
    application.add_handler(CommandHandler("user", bot_service.user))
    application.add_handler(CommandHandler("anyone", bot_service.anyone))
    application.add_handler(CommandHandler("block", bot_service.blocked))
    # setup required bot context
    application.bot_data['access_request'] = defaultdict(list)
    # run
    application.job_queue.run_once(bot_service._onstart, when=bot_service.get_parameter('DELAY', float, default=DefaultConfig.DELAY))
    application.run_polling()
    # close
    bot_service.close()
    # close logger
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)
    print('done.')
