import sqlalchemy as sa
import typing as t
import yaml
from ast import literal_eval


T = t.TypeVar('T')


class Permission:
    BLOCKED = 0x00
    USER =    0x01
    ADMIN =   0x02
    MASTER =  0x04
    MODERATOR = ADMIN | MASTER
    REGISTRED = USER | ADMIN | MASTER


class BotConfiguration:
    __slots__ = (
        '__config',
        '__defaults',
    )
    def __init__(self, config: t.Mapping[str, t.Any]):
        self.__config = config
        with open('defaults.yaml', 'r', encoding='utf-8') as file:
            self.__defaults = yaml.safe_load(file)

    def get(self,
            name: str,
            dtype: t.Type[T] | None = None
            ) -> T:
        """ Get parameter casted as type

        Parameters
        ----------
        name : str
            Parameter name
        dtype : type
            Parameter required type
        """
        try:
            try:
                value = literal_eval(self.__config[name])
            except:
                value = self.__config[name]
        except:
            value = self.__defaults[name]
        try:
            return dtype(value) if dtype else value
        except:
            return value

    def __getattr__(self,
                    name: str
                    ) -> t.Any:
        """ Get parameter `as is` without casting """
        return self.get(name)

    def __getitem__(self,
                    name: str
                    ) -> t.Any:
        """ Get parameter `as is` without casting """
        return self.get(name)


class BotDatabase:
    def __init__(self, connstr: str, schema: str):
        self.__engine = sa.create_engine(connstr)
        __meta = sa.MetaData()
        __meta.reflect(self.__engine, schema=schema)
        self.__tables = {tb.name: tb for tb in __meta.tables.values()}

    def load_configuration(self) -> BotConfiguration:
        """ Load bot configuration """
        with self.__engine.connect() as conn:
            config = {row.identifier: row.argument
                      for row in conn.execute(self.__tables['parameter'].select()).all()}
        return BotConfiguration(config)

    @t.overload
    def permission(self,
                   user_id: str | int,
                   ) -> bool | None:
        """ Get user access level. Return None if no such user found.

        Parameters
        ----------
        user_id : str or int
            Telegram user id
        """
    @t.overload
    def permission(self,
                   user_id: str | int,
                   *,
                   flag: int | None = None,
                   username: str | None = None,
                   ) -> bool | None:
        """ Set user access level and/or username.

        Parameters
        ----------
        user_id : str or int
            Telegram user id
        flag : bool | None
            If not None the given value would be set
        username : str | None
            If not None the given value would be set
        """
    def permission(self,
                   user_id: str | int,
                   **values
                   ) -> bool | None:
        tb = self.__tables['permission']
        with self.__engine.connect() as sql:
            query = sa.select(tb.c.flag).where(tb.c.user_id == user_id)
            row = sql.execute(query).first()
            if not values:
                return row.flag if row is not None else None
            if row is None:
                query = sa.insert(tb).values(user_id=user_id, **values)
            else:
                query = sa.update(tb).where(tb.c.user_id == user_id).values(**values)
            sql.execute(query)
            sql.commit()

    def admins(self) -> tuple[int, ...]:
        """ Get list of admin user_ids """
        tb = self.__tables['permission']
        with self.__engine.connect() as sql:
            query = sa.select(tb.c.user_id).where(tb.c.flag >= Permission.ADMIN)
            return tuple(row.user_id for row in sql.execute(query).all())

    def subscriptions(self, user_id: str | int) -> tuple[int, ...]:
        """ Get channel ids of active user subscriptions

        Parameters
        ----------
        user_id : str or int
            Telegram user id
        """
        tb = self.__tables['subscription']
        query = sa.select(tb.c.channel_id).where(tb.c.user_id == user_id, tb.c.active == 1)
        with self.__engine.connect() as sql:
            return tuple(row.channel_id for row in sql.execute(query).all())

    def subscribe(self,
                  user_id: str | int,
                  channel_id: str | int,
                  active: bool
                  ) -> None:
        """ Add or update user subscription

        Parameters
        ----------
        user_id : str or int
            Telegram user id
        channel_id : str or int
            Channel id
        active : bool
            New subscription status
        """
        tb = self.__tables['subscription']
        exists_query = tb.select().where(tb.c.user_id == user_id, tb.c.channel_id == channel_id)
        with self.__engine.connect() as sql:
            if sql.execute(exists_query).first() is None:
                query = sa.insert(tb).values(user_id=user_id, channel_id=channel_id, active=active)
            else:
                query = sa.update(tb).where(tb.c.user_id == user_id, tb.c.channel_id == channel_id).values(active=active)
            sql.execute(query)
            sql.commit()

    def subscribers(self, channel_id: str | int) -> tuple[int, ...]:
        """ Get ids of users subscribed for given channel

        Parameters
        ----------
        channel_id : str or int
            Channel id

        Return
        ------
        tuple of int
        """
        tb = self.__tables['subscription']
        query = sa.select(tb.c.user_id).where(tb.c.channel_id == channel_id, tb.c.active == 1)
        with self.__engine.connect() as sql:
            return tuple(row.user_id for row in sql.execute(query).all())

    def channels(self) -> t.Sequence[sa.Row]:
        """ Get active channels configuration """
        tb = self.__tables['channel']
        query = tb.select().where(tb.c.active == 1)
        with self.__engine.connect() as sql:
            return sql.execute(query).all()

    def users(self) -> tuple[int, ...]:
        """ Get all non-blocked users """
        tb = self.__tables['permission']
        with self.__engine.connect() as sql:
            query = sa.select(tb.c.user_id).where(tb.c.flag > Permission.BLOCKED)
            return tuple(row.user_id for row in sql.execute(query).all())

    def close(self):
        """ Dispose engine """
        self.__engine.dispose()
