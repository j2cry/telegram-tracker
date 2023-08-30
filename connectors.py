import os
import pathlib
import importlib
import datetime as dt
from enum import Enum
from itertools import chain
from abc import ABC, abstractmethod
import sqlalchemy as sa


class Connector:
    def __init__(self, cid: str | int, name: str, *, modified: dt.datetime = None, logger=None, **kwargs):
        """ Base connector initializer

        Parameters
        ----------
        cid : str or int
            Channel id
        name : str
            Channel visible name
        modified : datetime
            Last modified moment
        logger : ...

        """
        # common parameters
        self.cid = cid
        self.name = name
        # service parameters
        self.last_modified = modified if isinstance(modified, dt.datetime) else dt.datetime.now()
        self.logger = logger
        # other parameters
        for k, v in kwargs.items():
            setattr(self, k, v)

    @property
    def context(self):
        """ Return parameters which must be saved on connector rebuild """
        return {
            'modified': self.last_modified
        }

    @abstractmethod
    def check(self) -> tuple | tuple[str]:
        """ Check channel for updates and return all new messages """

    def close(self) -> None:
        """ Do something on closing connector """


class FileConnector(Connector):
    """ Listen on file for updates """
    def check(self):
        if not os.path.exists(self.path):
            return tuple()
        # get last datetime file was modified
        modified = dt.datetime.fromtimestamp(os.path.getmtime(self.path))
        if self.last_modified >= modified:
            return tuple()
        try:
            with open(self.path, 'r', encoding=getattr(self, 'encoding', 'utf-8')) as file:
                content = file.read().strip()
        except Exception as ex:
            content = f"Can't read the target file `{self.path}`: {ex}"
        self.last_modified = modified
        return (content,)


class FolderConnector(Connector):
    """ Listen on folder for changes """
    class TriggerOn(Enum):
        ADD = 0x01
        DEL = 0x02
        ANY = 0x03

    showfuncMap = {
        'LIST': lambda files: '\n' + '\n'.join(files),
        'COUNT': lambda files: f' {len(files)}'
    }

    def __init__(self, cid: str | int, name: str, *, modified: dt.datetime = None, files: tuple = None, logger=None, **kwargs):
        super().__init__(cid, name, modified=modified, logger=logger, **kwargs)
        self.trigger = self.TriggerOn[kwargs.get('trigger', 'ANY').upper()].value
        self.showfunc = self.showfuncMap[kwargs.get('show', 'COUNT').upper()]
        # collect files on first run
        self.files = files if isinstance(files, tuple) else \
            tuple(*chain((pathlib.Path(path, name).as_posix() for name in filenames) for path, _, filenames in os.walk(self.path)))

    @property
    def context(self):
        return {
            'modified': self.last_modified,
            'files': self.files
        }

    def check(self):
        if not os.path.exists(self.path):
            self.files = tuple()
            return self.files
        files = []
        for path, _, filenames in os.walk(self.path):
            files.extend(pathlib.Path(path, name).as_posix() for name in filenames)
        # skip first run
        content = []
        if self.files is not None:
            # check for updates
            if (_files := set(self.files).difference(files)) and (self.trigger & self.TriggerOn.DEL.value):
                content.append(f'Removed files:{self.showfunc(_files)}')
            if (_files := set(files).difference(self.files)) and (self.trigger & self.TriggerOn.ADD.value):
                content.append(f'Added files:{self.showfunc(_files)}')
            self.last_modified = dt.datetime.now()
        # remember state
        self.files = tuple(files)
        return tuple(content)


class SQLConnector(Connector):
    """ Listen on SQL table for updates """
    def __init__(self, cid: str | int, name: str, *, connstr: str, modified: dt.datetime = None, logger=None, **kwargs):
        super().__init__(cid, name, modified=modified, logger=logger, **kwargs)
        self.__engine = sa.create_engine(connstr)
        schema, name = kwargs['table'].split('.')
        self.table = sa.table(name, schema=schema)
        self.order = sa.column(kwargs['order'])

    def close(self):
        """ Close SQL connection """
        self.__engine.dispose()

    def check(self) -> tuple:
        query = sa.select('*').select_from(self.table).where(self.order > self.last_modified).order_by(self.order)
        with self.__engine.connect() as sql:
            rows = tuple(row._asdict() for row in sql.execute(query).all())
        self.__engine.dispose()
        if not rows:
            return tuple()
        self.last_modified = max((row[self.order.name] for row in rows))
        content = tuple('\n'.join(f'{k} = {v}' for k, v in row.items() if k != self.order.name) for row in rows)
        return content

class ConnectorMap(Enum):
    """ Implemented connectors map for channels.cnf """
    FILE = FileConnector
    FOLDER = FolderConnector
    SQL = SQLConnector
