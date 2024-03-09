import datetime as dt
import enum
import os
import pathlib
import sqlalchemy as sa
import typing as t
from abc import ABC, abstractmethod


class Connector:
    def __init__(self,
                 channel_id: str | int,
                 name: str,
                 *,
                 modified: dt.datetime | None = None,
                 logger=None,
                 **kwargs):
        """ Base connector initializer

        Parameters
        ----------
        channel_id : str or int
            Channel id
        name : str
            Channel visible name
        modified : datetime
            Last modified moment
        logger : ...

        """
        # common parameters
        self.channel_id = int(channel_id)
        self.name = name
        # service parameters
        self.last_modified = modified if isinstance(modified, dt.datetime) else dt.datetime.now()
        self.__logger = logger
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
    def check(self) -> tuple[str, ...]:
        """ Check channel for updates and return all new messages """

    def close(self) -> None:
        """ Do something on closing connector """


class FileConnector(Connector):
    """ Listen on file for updates """
    path: str

    def check(self) -> tuple[str, ...]:
        # assert hasattr(self, 'path'), "Incorrect connector configuration: path is required"
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


class FoldersConnector(Connector):
    paths: t.Sequence[str]

    def __init__(self,
                 channel_id: str | int,
                 name: str,
                 *,
                 modified: dt.datetime | None = None,
                 files: t.MutableMapping[str, set[str]] | None = None,
                 logger=None,
                 **kwargs):
        # collect files on first run
        self.files = (files if isinstance(files, t.MutableMapping)
                      else {p: self.__collect_folder_content(p) for p in kwargs['paths']})
        super().__init__(channel_id, name, modified=modified, logger=logger, **kwargs)

    @staticmethod
    def __collect_folder_content(path: str) -> set[str]:
        """ Collect files list tree in specified folder """
        files = set()
        for p, _, filenames in os.walk(path):
            files.update(pathlib.Path(p, name).as_posix() for name in filenames)
        return files

    def check(self) -> tuple[str, ...]:
        """ Check specified folders for new files """
        content = []
        for path in set(self.paths):
            _files = self.__collect_folder_content(path)
            # check for folder content changes
            added = _files.difference(self.files[path])
            removed = self.files[path].difference(_files)
            if added or removed:
                content.append(f'[{path}]\n'
                               f'added {len(added)} file(s);\n'
                               f'removed {len(removed)} file(s);')
            # remember folder content
            self.files[path] = _files
            self.last_modified = dt.datetime.now()
        return tuple(content)

    @property
    def context(self):
        return {
            'modified': self.last_modified,
            'files': self.files
        }


class SQLConnector(Connector):
    """ Listen on SQL table for updates """
    def __init__(self,
                 channel_id: str | int,
                 name: str,
                 *,
                 connstr: str,
                 modified: dt.datetime | None = None,
                 logger=None,
                 **kwargs):
        self.__engine = sa.create_engine(connstr)
        schema, tbname = kwargs.pop('table').split('.')
        self.table = sa.table(tbname, schema=schema)
        self.order = sa.column(kwargs.pop('order'))
        super().__init__(channel_id, name, modified=modified, logger=logger, **kwargs)

    def close(self):
        """ Close SQL connection """
        self.__engine.dispose()

    def check(self) -> tuple[str, ...]:
        query = sa.select('*').select_from(self.table).where(self.order > self.last_modified).order_by(self.order)
        with self.__engine.connect() as sql:
            rows = tuple(row._asdict() for row in sql.execute(query).all())
        self.__engine.dispose()
        if not rows:
            return tuple()
        self.last_modified = max((row[self.order.name] for row in rows))
        content = tuple(f'[{row[self.order.name].strftime("%d.%m.%Y %H:%M:%S")}]\n' +
                        '\n'.join(f'{k} = {v}'
                        for k, v in row.items() if k != self.order.name)
                        for row in rows)
        return content


class ConnectorMap(enum.Enum):
    """ Implemented connectors map for channels.cnf """
    FILE = FileConnector
    FOLDERS = FoldersConnector
    SQL = SQLConnector
