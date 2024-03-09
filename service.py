# from __future__ import annotations
import asyncio
import enum
import datetime as dt
import httpx
import json
import logging
import math
import os
import random
import re
import signal
import sqlalchemy.exc as sqlex
import time
import traceback
import typing as t
from ast import literal_eval
from collections import namedtuple
from functools import wraps
from telegram import Message, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackContext, ContextTypes, Job

import logging
from logging.handlers import RotatingFileHandler

from connectors import Connector, ConnectorMap
from database import BotDatabase, Permission
from __version__ import VERSION


T = t.TypeVar('T')
HandlerCallback = t.Callable[['t.Self', Update, CallbackContext], t.Coroutine[t.Any, t.Any, T]] # type: ignore


class JobState(enum.IntEnum):
    SUSPENDED = -1
    RESUMED = 1
JobInfo = namedtuple('JobInfo', 'name,context,state', defaults=(None, {}, None))
InlineCallback = namedtuple('InlineCallback', 'mark,value,extra', defaults=(None, None, None))

class ContextSegment(enum.StrEnum):
    ACCESS_REQUEST = enum.auto()
    SILENT_MODE = enum.auto()
    SUBSCRIPTION = enum.auto()


class CheckMark(enum.StrEnum):
    TRUE = '\u2714'
    FALSE = '\u2716'


class BotService:
    class NoneUser:
        id = None
        username = 'n/a'

    def __init__(self,
                 connstr: str,
                 schema: str = 'TRACKER',
                 ):
        # init service connection
        self.__db = BotDatabase(connstr, schema)
        # read configuration
        self.cf = self.__db.load_configuration()
        # initialize logger
        if (dirname := os.path.dirname(self.cf.LOGFILE)) and not os.path.exists(dirname):
            os.makedirs(dirname)
        log_handler = RotatingFileHandler(
            self.cf.LOGFILE,
            mode='w',
            maxBytes=self.cf.LOGSIZE,
            backupCount=self.cf.LOGBACKUP,
            encoding='utf-8'
        )
        log_handler.setFormatter(logging.Formatter('[%(levelname)s] %(asctime)s > %(message)s'))
        log_handler.setLevel(self.cf.LOGLEVEL)
        self.__logger = logging.getLogger('TelegramTrackerService')
        self.__logger.setLevel(self.cf.LOGLEVEL)
        self.__logger.addHandler(log_handler)
        self.__logger.info('Logger initialized')
        self.__admins = self.__db.admins()  # remember admins for crash notifications

    def close(self):
        """ Finalize service """
        self.__logger.info('SIGTERM was sent.')
        for handler in self.__logger.handlers:
            handler.close()
            self.__logger.removeHandler(handler)

    @staticmethod
    def parse_interval(value: str) -> float | dt.time | None:
        """ Job time parser. Returns parsed value of first applicable format or None. """
        formatter = (float, dt.time.fromisoformat)
        for func in formatter:
            try:
                return func(value)
            except:
                ...

    @staticmethod
    def logcommand(func: HandlerCallback) -> HandlerCallback:
        """ Log command decorator """
        @wraps(func)
        def _wrapper(self: t.Self, update: Update, context: CallbackContext) -> t.Coroutine[t.Any, t.Any, t.Any]:
            user = update.effective_user or self.NoneUser
            with_args = f" with args `{' '.join(context.args)}`" if context.args else ''
            self.__logger.info(f'[COMMAND] Command `{func.__name__}`{with_args} was accepted from {user.id} [{user.username}]')
            return func(self, update, context)
        return _wrapper

    @staticmethod
    def access(permissions: int = Permission.MASTER):
        """ Command access level decorator """
        def _decorator(func: HandlerCallback) -> HandlerCallback:
            @wraps(func)
            def _wrapper(self: t.Self, update: Update, context: CallbackContext) -> t.Coroutine:
                assert (user := update.effective_user) is not None, f"[{func.__name__}] User id is invalid"
                # request and compare user permissions
                if (flag := self.__db.permission(user.id)) is None:
                    return self.start(update, context)
                elif permissions & flag:
                    return func(self, update, context)
                else:
                    self.__logger.info(f'[COMMAND] Command `{func.__name__}` was rejected for {user.id} [{user.username}]')
                    return self.__reply(update, context, 'MESSAGE_NOT_ALLOWED', flag=flag)
            return _wrapper
        return _decorator

    # ========================= ADDITIONAL METHODS =========================
    async def __send_by_parts(self,
                              context: CallbackContext,
                              subscriber: str | int,
                              message: str,
                              **kwargs
                              ):
        """ Send message by parts """
        LENGTH = self.cf.TEXT_MAX_LENGTH
        LIFETIME = self.cf.NOTIFICATION_LIFETIME
        INTERVAL = self.cf.RETRY_INTERVAL
        started = time.monotonic()
        attempt = 1
        for bound in range(0, len(message), LENGTH):
            part = message[bound:bound + LENGTH]
            while time.monotonic() - started <= LIFETIME:
                try:
                    await context.bot.send_message(subscriber, part, **kwargs)
                    self.__logger.debug(f'Message sent [{subscriber}]: {part}')
                    break
                except:
                    tb = traceback.format_exc()
                    self.__logger.error(f'{attempt=}; Message not sent [{subscriber}]: {part} due to {tb}')
                    await asyncio.sleep(INTERVAL)
            else:
                self.__logger.error(f'[EXPIRED] Message not sent [{subscriber}]: {part}')

    def __sendall(self,
                  context: CallbackContext,
                  subscribers: str | int | t.Sequence[str | int],
                  messages: str | t.Sequence[str],
                  **kwargs
                  ) -> t.Iterable[asyncio.Future]:
        # recast arguments
        if isinstance(subscribers, (str, int)):
            subscribers = (subscribers,)
        if isinstance(messages, (str, int)):
            messages = (messages,)
        # send messages
        sent = []
        for message in messages:
            for user_id in subscribers:
                _coro = self.__send_by_parts(context, user_id, message, **kwargs)
                sent.append(asyncio.create_task(_coro))
        return sent

    async def __reply(self,
                     update: Update,
                     context: CallbackContext,
                     name: str,
                     **optional: t.Any
                     ) -> Message:
        """ Send static reply """
        assert update.message is not None, f"Message is invalid"
        return await update.message.reply_text(self.cf[name].format(**optional))

    # ========================= SYSTEM JOBS =========================
    async def __actualize(self, context: CallbackContext):
        """ Syncronize listener jobs with actual channels list """
        assert context.job_queue is not None, f"Job queue is invalid"
        # reschedule actualizer job
        interval = self.parse_interval(self.cf.ACTUALIZE_INTERVAL)
        assert interval is not None, "Actualize interval parameter is invalid"
        if isinstance(interval, dt.time):
            interval = interval.replace(tzinfo=context.job_queue.scheduler.timezone)
        if isinstance(interval, dt.time) or interval > 0:
            for job in context.job_queue.get_jobs_by_name('actualize'):
                job.schedule_removal()
            job = context.job_queue.run_once(self.__actualize, when=interval, name='actualize')
            self.__logger.info(f'Actualizer job scheduled @{job.next_t}.')
        # actualize channels
        channels: dict[int, JobInfo] = {}   # collection of enabled and disabled channels
        # remove scheduled listeners
        for job in context.job_queue.jobs():
            if job.name and not job.name.startswith('listener'):
                continue
            if isinstance(job.data, Connector):
                channels[job.data.channel_id] = JobInfo(job.data.name, job.data.context, -1)
                job.data.close()
            job.schedule_removal()
        # update channel connectors
        for channel in self.__db.channels():
            # parse connector parameters
            connectorClass: type[Connector] = ConnectorMap[channel.connector.upper()].value
            try:
                config = json.loads(channel.config)
            except:
                self.__logger.error(f'Cannot parse channel [{channel.identifier}] configuration')
                continue
            connector = connectorClass(channel.channel_id,
                                       channel.identifier,
                                       logger=self.__logger,
                                       **config,
                                       **channels.get(channel.channel_id, JobInfo()).context
                                       )
            # schedule listener job
            jobtime = self.parse_interval(channel.polling) or self.parse_interval(self.cf.POLLING)
            assert jobtime is not None, f"Channel [{channel.identifier}] Job time is invalid"
            if isinstance(jobtime, dt.time):
                job = context.job_queue.run_daily(self.__listen,
                                                  time=jobtime,
                                                  name=f'listener{connector.channel_id}',
                                                  data=connector)
            else:
                job = context.job_queue.run_repeating(self.__listen,
                                                      interval=jobtime,
                                                      name=f'listener{connector.channel_id}',
                                                      data=connector)
            self.__logger.info(f'Listener job for {channel.identifier} scheduled @{job.next_t}')
            # update channels collection
            state = (channels[channel.channel_id].state + 1) if channel.channel_id in channels else 1
            channels[channel.channel_id] = JobInfo(channel.identifier, connector, state)
        # send notifications
        if not self.cf.SILENT_ACTUALIZE:
            sent = []
            for channel_id, jobinfo in channels.items():
                match jobinfo.state:
                    case JobState.RESUMED:
                        NOTIFICATION = self.cf.MESSAGE_RESUME_SUBSCRIPTION.format(name=jobinfo.name)
                    case JobState.SUSPENDED:
                        NOTIFICATION = self.cf.MESSAGE_SUSPEND_SUBSCRIPTION.format(name=jobinfo.name)
                    case _:
                        continue
                sent.extend(self.__sendall(context,
                                           self.__db.subscribers(channel_id),
                                           NOTIFICATION))
            if sent:
                await asyncio.wait(sent)
        self.__logger.info('[SYSTEM] Channels actualized')

    async def __listen(self, context: CallbackContext):
        """ Check for channel updates and send notifications """
        assert context.job is not None and isinstance(context.job.data, Connector)
        self.__logger.info(f'Listener job for {context.job.data.name} scheduled @{context.job.next_t}')
        connector: Connector = context.job.data
        messages = connector.check()
        if not messages:
            self.__logger.info(f'Channel {connector.channel_id} ({connector.name}) has no updates.')
            return
        sent = []
        for user_id in self.__db.subscribers(connector.channel_id):
            SLEEP_TIME = context._application.user_data[user_id].get('SLEEP_TIME')
            if SLEEP_TIME and SLEEP_TIME > dt.datetime.now():
                self.__logger.info(f'[SKIP] Notifications for subscriber {user_id} are disabled until {SLEEP_TIME}')
                continue
            sent.extend(self.__sendall(context, user_id, messages))
        await asyncio.wait(sent)
        self.__logger.info(f'Channel {connector.channel_id} ({connector.name}) update succeed.')

    async def __access_autoreject(self, context: CallbackContext):
        """ Reject access request after timeout """
        messages = context.bot_data[ContextSegment.ACCESS_REQUEST][context._user_id]
        answer = self.cf.MESSAGE_REQUEST_REJECTED.format(username=context.bot.name)
        for message in messages:
            await message.edit_text(answer)
        context.bot_data[ContextSegment.ACCESS_REQUEST][context._user_id] = []
        await context.bot.send_message(context._user_id, answer)

    async def __silent_off(self, context: CallbackContext):
        """ Silent mode was ended notification """
        assert context.user_data is not None, "[SILENT_OFF] User context is invalid"
        assert context._user_id is not None, "[SILENT_OFF] User ID is invalid"
        context.user_data[ContextSegment.SILENT_MODE] = None
        await context.bot.send_message(context._user_id, self.cf.MESSAGE_NOTIFICATION_ENABLED)

    # ========================= SYSTEM HANDLERS =========================
    async def _onstart(self, context: CallbackContext):
        """ Start log listeners """
        await self.__actualize(context)

    async def _onclose(self, context: CallbackContext):
        """ Stop log listeners and kill self """
        if context.job_queue is not None:
            context.job_queue.scheduler.remove_all_jobs()   # cancel all jobs
            self.__logger.info('All jobs have been removed.')
        self.__db.close()
        self.__logger.info('Database engine disposed.')
        os.kill(os.getpid(), signal.SIGTERM)

    async def _onerror(self, update: object, context: CallbackContext):
        """ Error handler """
        match context.error:
            case httpx.NetworkError():
                self.__logger.error(f'[ERROR] Network error ({context.error})')
            case AssertionError():
                if sent := self.__sendall(context, self.__admins, self.cf.MESSAGE_ASSERTION):
                    await asyncio.wait(sent)
                self.__logger.error(f'[ERROR] {context.error}: {traceback.format_exc()}')
            case sqlex.OperationalError():
                if 'timed out' in str(context.error):
                    name = (context.job.data.name
                            if context.job and isinstance(context.job.data, Connector)
                            else 'unknown')
                    if sent := self.__sendall(context,
                                              self.__admins,
                                              self.cf.MESSAGE_SQL_CONNECTION_LOST.format(name=name)):
                        await asyncio.wait(sent)
                self.__logger.error(f'[ERROR] SQL operational error {context.error}: {traceback.format_exc()}')
            case _:
                self.__logger.error(f'[ERROR] {context.error}: {traceback.format_exc()}')

    # ========================= INLINES =========================
    @access(Permission.MASTER | Permission.ADMIN)
    @logcommand
    async def access_response(self, update: Update, context: CallbackContext):
        """ Send access response """
        assert update.callback_query is not None, "Callback has no query or query is invalid"
        assert (admin := update.effective_user) is not None, "Effective user is invalid"
        assert context.job_queue is not None, "Job queue is invalid"
        # parse callback
        await update.callback_query.answer()
        data = InlineCallback(*(update.callback_query.data or '').split(',')[1:])
        if data.mark == 'APPROVED':
            self.__db.permission(data.value, flag=Permission.USER, username=data.extra)
        # clean context
        messages = context.bot_data[ContextSegment.ACCESS_REQUEST][int(data.value)]
        context.bot_data[ContextSegment.ACCESS_REQUEST][int(data.value)] = []
        for job in context.job_queue.get_jobs_by_name(f'access{data.value}'):
            job.schedule_removal()
        # send messages
        TEXT = self.cf[f'MESSAGE_REQUEST_{data.mark}'].format(username=admin.name or admin.id)
        for message in messages:
            await message.edit_text(TEXT, reply_markup=None)
        await context.bot.send_message(data.value, TEXT)

    # ========================= COMMANDS =========================
    async def start(self, update: Update, context: CallbackContext):
        """ Start bot interactions with sending access request """
        assert (user := update.effective_user) is not None, "[START] Invalid user"
        assert context.job_queue is not None, "Job queue is invalid"
        # if user already registred
        if (flag := self.__db.permission(user.id)) is not None:
            if user.username:
                self.__db.permission(user.id, username=user.username)
            return await self.__reply(update, context, 'MESSAGE_ALREADY_ACCESSIBLE', flag=flag)
        # otherwise check if access requests exist
        messages = context.bot_data[ContextSegment.ACCESS_REQUEST][user.id]
        if messages:
            jobs = context.job_queue.get_jobs_by_name(f'access{user.id}')
            REQUEST_EXPIRATION_TIME = max((job.next_t for job in jobs if job.next_t))
        else:
            ADMINS = self.__db.admins()
            if not ADMINS:    # if there is no admins yet
                self.__db.permission(user.id, flag=Permission.MASTER, username=user.username)
                return await self.__reply(update, context, 'MESSAGE_REQUEST_APPROVED', username=context.bot.name)
            # prepare inline
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(CheckMark['FALSE'], callback_data=f'access,REJECTED,{user.id},{user.username}'),
                InlineKeyboardButton(CheckMark['TRUE'], callback_data=f'access,APPROVED,{user.id},{user.username}')
            ]])
            # send notifications to all admins
            REQUEST_EXPIRATION_TIME = (dt.datetime.now().replace(microsecond=0) +
                                       dt.timedelta(seconds=self.cf.ACCESS_REQUEST_MAXTIME))
            TEXT = self.cf.MESSAGE_ACCESS_REQUEST_TEXT.format(username=user.username or user.id, maxtime=REQUEST_EXPIRATION_TIME)
            for admin_id in ADMINS:
                messages.append(await context.bot.send_message(admin_id, TEXT, reply_markup=markup))
            context.job_queue.run_once(self.__access_autoreject,
                                       when=REQUEST_EXPIRATION_TIME,
                                       name=f'access{user.id}',
                                       user_id=user.id)
        await self.__reply(update, context, 'MESSAGE_ACCESS_REQUESTED', maxtime=REQUEST_EXPIRATION_TIME)

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def subscript(self, update: Update, context: CallbackContext):
        """ Configure individual subscriptions """
        assert (user := update.effective_user) is not None, "Effective user is invalid"
        assert context.user_data is not None, "User context is invalid"
        # parse callback data
        if update.callback_query is None:
            data = InlineCallback('0')
        else:
            await update.callback_query.answer()
            data = InlineCallback(*(update.callback_query.data or '').split(',')[1:])
        message = context.user_data.get(ContextSegment.SUBSCRIPTION)
        match data:
            case 'ok', *_:
                # pressed OK button
                if message is not None:
                    await message.delete()
                    context.user_data[ContextSegment.SUBSCRIPTION] = None
                return
            case str(page), None, *_:
                # pressed navigation button or first opened
                page = int(page)
                channel_id = None
            case str(page), str(channel_id), str(flag):
                # pressed channel button
                page = int(page)
                self.__db.subscribe(user.id, channel_id, literal_eval(flag))
            case _:
                raise AttributeError('Incorrect callback format')
        # get active channels and personal subscriptions
        channels = self.__db.channels()
        subscriptions = self.__db.subscriptions(user.id)
        # refresh subscriptions menu
        MAXPAGE = math.ceil(len(channels) / self.cf.CHANNELS_PER_PAGE) - 1
        if page < 0:
            page = MAXPAGE
        elif page > MAXPAGE:
            page = 0
        markup = InlineKeyboardMarkup((
            # build channels list
            *((InlineKeyboardButton(f'{CheckMark[str(ch.channel_id in subscriptions).upper()]} {ch.identifier}',
                                    callback_data=f'subscript,{page},{ch.channel_id},{ch.channel_id not in subscriptions}'),)
                for ch in channels[self.cf.CHANNELS_PER_PAGE * page:self.cf.CHANNELS_PER_PAGE * (page + 1)]),
            # build navigation buttons
            (InlineKeyboardButton('<<', callback_data=f'subscript,{page - 1}'),
             InlineKeyboardButton('>>', callback_data=f'subscript,{page + 1}')),
            # build OK button
            (InlineKeyboardButton('OK', callback_data='subscript,ok'),),
        ))
        TEXT = self.cf.UI_SUBSCRIPTIONS_MENU_HEADER.format(page=page + 1, total=MAXPAGE + 1)
        if message is None:
            context.user_data[ContextSegment.SUBSCRIPTION] = await context.bot.send_message(user.id, TEXT, reply_markup=markup)
        elif MAXPAGE or channel_id is not None:
            await message.edit_text(TEXT, reply_markup=markup)

    @access(Permission.MASTER | Permission.ADMIN)
    @logcommand
    async def actualize(self, update: Update, context: CallbackContext):
        """ Syncronize listener jobs with actual channels list """
        await self.__actualize(context)
        await self.__reply(update, context, 'MESSAGE_ACTUALIZE_TEXT')

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def check(self, update: Update, context: CallbackContext):
        """ Check for channel updates immediately without affecting on scheduled jobs """
        assert context.job_queue is not None, "Job queue is invalid"
        message = await self.__reply(update, context, 'MESSAGE_CHECK_TEXT')
        for job in context.job_queue.jobs():
            if job.name and job.name.startswith('listener'):
                await job.run(context.application)
        await message.reply_text(self.cf.MESSAGE_DONE)

    @access(Permission.MASTER | Permission.ADMIN)
    async def state(self, update: Update, context: CallbackContext):
        """ Return current Tracker state """
        def jobformat(job: Job):
            """ Return formatted job schedule """
            next_t = job.next_t.replace(microsecond=0, tzinfo=None) if job.next_t else None
            return f'{getattr(job.data, "name", job.name)} {next_t}'
        assert context.job_queue is not None, "Job queue is invalid"
        assert update.effective_user is not None, "Invalid user"
        TIMESTAMP = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        STATE = f'[{TIMESTAMP}] Self state:\n' + '\n'.join(map(jobformat, context.job_queue.jobs()))
        await context.bot.send_message(update.effective_user.id, STATE)

    @access(Permission.MASTER | Permission.ADMIN)
    @logcommand
    async def reload(self, update: Update, context: CallbackContext):
        """ Reload bot configuration """
        assert update.effective_user is not None, "Invalid user"
        self.cf = self.__db.load_configuration()
        await context.bot.send_message(update.effective_user.id, self.cf.MESSAGE_RELOAD_CONFIGURATION)

    @access(Permission.MASTER | Permission.ADMIN | Permission.USER)
    @logcommand
    async def silent(self, update: Update, context: CallbackContext):
        """ Silent all notifications """
        assert context.user_data is not None, "User context is invalid"
        assert context.job_queue is not None, "Job queue is invalid"
        assert update.effective_user is not None, "Invalid user"
        def get_delta_params(source):
            """ Timedelta argument parser """
            class KeyMapper(enum.StrEnum):
                d = 'days'
                h = 'hours'
                m = 'minutes'
                s = 'seconds'
            return {KeyMapper[item[1]]: int(item[0])
                    for item in re.findall(rf'(\d+)({"|".join(KeyMapper._member_names_)})', source)}
        # used argument parsers
        formatter = (
            dt.datetime.fromisoformat,
            lambda arg: dt.datetime.fromisoformat(f'{dt.date.today()} {dt.time.fromisoformat(arg)}'),
            lambda arg: dt.datetime.now().replace(microsecond=0) + dt.timedelta(**get_delta_params(arg))
        )
        timearg = ' '.join(context.args or ())
        SLEEP_TIME = None
        for func in formatter:
            try:
                SLEEP_TIME = func(timearg)
                break
            except:
                ...
        if SLEEP_TIME is None or SLEEP_TIME <= dt.datetime.now():
            return await self.__reply(update, context, 'MESSAGE_WRONG_ARGUMENT')
        # setup context
        context.user_data[ContextSegment.SILENT_MODE] = SLEEP_TIME
        delta = SLEEP_TIME - dt.datetime.now()
        context.job_queue.run_once(self.__silent_off, when=delta, user_id=update.effective_user.id)
        await self.__reply(update, context, 'MESSAGE_NOTIFICATION_DISABLED', sleeptime=SLEEP_TIME)

    @access(Permission.MASTER)
    @logcommand
    async def shutdown(self, update: Update, context: CallbackContext):
        """ Shutdown Tracker """
        assert context.job_queue is not None, "Job queue is invalid"
        await self.__reply(update, context, 'MESSAGE_SHUTDOWN_TEXT')
        context.job_queue.run_once(self._onclose, when=self.cf.UPDOWN_DELAY)

    @access(Permission.REGISTRED)
    @logcommand
    async def version(self, update: Update, context: CallbackContext):
        """ Get tracker version """
        return await self.__reply(update, context, VERSION)

    @access(Permission.REGISTRED)
    @logcommand
    async def fox(self, update: Update, context: CallbackContext):
        """ Send a fox smile to random user """
        assert update.message is not None, f"Invalid message"
        assert update.effective_user is not None, "Invalid effective user"
        user_id = random.choice([uid for uid in self.__db.users() if uid != update.effective_user.id])
        await update.message.reply_text(f'Sent to {user_id}')
        await context.bot.send_message(user_id, '🦊')

    @access(Permission.MASTER)
    @logcommand
    async def debug(self, update: Update, context: CallbackContext):
        """ Debug feature """
