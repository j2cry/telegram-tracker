import os
import pathlib
import importlib
import datetime as dt
from enum import Enum


class Connector:
    def __init__(self, cid: str|int, name: str, *, modified: dt.datetime=None, logger=None, **kwargs):
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
        self.last_modified = modified if modified is not None and isinstance(modified, dt.datetime) else dt.datetime.now()
        self.logger = logger
        # other parameters
        for k, v in kwargs.items():
            setattr(self, k, v)

    def check(self) -> tuple:
        """ Check channel for updates and return all new messages
        
        Return
        ------
        tuple of str
        """
        # NOTE for implementation in nested connectors
    
    def close(self) -> None:
        """ Do something on closing application """
        # for implementation in nested connectors


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

    def __init__(self, cid: str | int, name: str, *, modified: tuple = None, logger=None, **kwargs):
        super().__init__(cid, name, modified=modified, logger=logger, **kwargs)
        self.trigger = self.TriggerOn[kwargs.get('trigger', 'ANY').upper()].value
        self.showfunc = self.showfuncMap[kwargs.get('show', 'COUNT').upper()]
        self.last_modified = modified if modified is not None and isinstance(modified, tuple) else None

    def check(self):
        if not os.path.exists(self.path):
            self.last_modified = tuple()
            return self.last_modified
        files = []
        for path, dirnames, filenames in os.walk(self.path):
            files.extend(pathlib.Path(path, name).as_posix() for name in filenames)
        # skip first run
        content = []
        if self.last_modified is not None:
            # check for updates
            if (_files := set(self.last_modified).difference(files)) and (self.trigger & self.TriggerOn.DEL.value):
                content.append(f'Removed files:{self.showfunc(_files)}')
            if (_files := set(files).difference(self.last_modified)) and (self.trigger & self.TriggerOn.ADD.value):
                content.append(f'Added files:{self.showfunc(_files)}')
        # remember state
        self.last_modified = tuple(files)
        return tuple(content)


class SQLConnector(Connector):
    """ Listen on SQL table for updates """
    def __init__(self, cid: str | int, name: str, *, modified: dt.datetime = None, logger=None, **kwargs):
        super().__init__(cid, name, modified=modified, logger=logger, **kwargs)
        self.state = self.connect()
    
    def connect(self):
        """ Establish SQL connection """
        try:
            engine = importlib.import_module(self.engine)
            self.__conn = engine.connect(server=self.server,
                                         database=self.database,
                                         user=getattr(self, 'user', None),
                                         password=getattr(self, 'password', None),
                                         charset=getattr(self, 'charset', 'UTF-8'),
                                         as_dict=True)
            self.__cursor = self.__conn.cursor()
        except Exception as ex:
            return ex
    
    def close(self):
        """ Close SQL connection """
        self.__cursor.close()
        self.__conn.close()
    
    def check(self) -> tuple:
        if self.state:
            return (str(self.state),)
        for attempt in range(2):
            try:
                self.__cursor.execute(f'SELECT * FROM {self.table} WHERE {self.order} > %s ORDER BY {self.order}', params=(self.last_modified,))
                rows = self.__cursor.fetchall()
                break
            except Exception as ex:
                self.state = self.connect()
                if self.state:
                    return (str(self.state),)
                elif attempt:
                    return (str(ex),)
                continue
        if not rows:
            return tuple()
        self.last_modified = max([row[self.order] for row in rows])
        content = tuple(', '.join(f'{k} = {v}' for k, v in row.items() if k != self.order) for row in rows)
        return content

class ConnectorMap(Enum):
    """ Implemented connectors map for channels.cnf """
    FILE = FileConnector
    FOLDER = FolderConnector
    SQL = SQLConnector
