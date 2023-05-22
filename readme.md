# Telegram Tracker
The service is designed for tracking logs in files, folders and SQL tables with notification in the Telegram messenger.

## Design
### `TRACKER.parameter`
Contains general key:value parameters

| identifier         | description                                                    |
|--------------------|----------------------------------------------------------------|
| TOKEN              | Telegram API token                                             |
| REQUEST_MAXTIME    | The time during which the access request is valid (in seconds) |
| READ_TIMEOUT       | Waiting time for sending a message                             |
| CHANNELS_PER_PAGE  | Number of items per page in subscriptions menu                 |
| DELAY              | Timeout before starting and stopping listeners                 |
| ACTUALIZE_INTERVAL | Interval between connector configurations reload               |

### `TRACKER.permission`
Contains user access level flags.

There are 4 levels of access. Each of them initially *does not include* the previous ones. Therefore, when adding new commands, it is possible to flexibly configure access levels to them.

| code |  level  | description        |
|------|---------|--------------------|
| NULL | ANYONE  | No record in table |
| 0x00 | BLOCKED | User is blocked    |
| 0x01 | USER    | Default user       |
| 0x02 | ADMIN   | Administrator      |
| 0x04 | MASTER  | Master             |

If there is no MASTER in this table, the first user who enters the `/start` command will be automatically assigned as MASTER.

### `TRACKER.channel`
Contains configurations for existing connectors

| column     | description                                                          |
|------------|----------------------------------------------------------------------|
| identifier | Channel visible name                                                 |
| connector  | Used connector                                                       |
| config     | Stringified JSON with required parameters                            |
| polling    | Polling interval                                                     |
| active     | Relevance of the record                                              |

### `TRACKER.subscription`
Contains information about individual subscriptions.


## Commands
| command      | access level            | description                                                   |
|--------------|-------------------------|---------------------------------------------------------------|
| `/start`     | NULL                    | Basic public command. Request access permission for new users |
| `/version`   | NULL                    | Public command. Returns the current Tracker version.          |
| `/subscript` | USER \| ADMIN \| MASTER | Open user subscriptions configuration menu                    |
| `/silent`    | USER \| ADMIN \| MASTER | Temporarily disable notifications                             |
| `/check`     | USER \| ADMIN \| MASTER | Immediately check channels for updates                        |
| `/actualize` | ADMIN \| MASTER         | Immediately actualize connectors configuration                |
| `/shutdown`  | MASTER                  | Stop listeners and shutdown Tracker                           |


## Build and deploy
The project intentionally does not contain any build and deployment ways. You can develop the necessary one based on your needs. There are some ideas below:
1. pyinstaller for Windows standalone
2. docker for Linux/Windows

## Connectors configuration
Connector parameters should be passed as stringified JSON to `TRACKER.channel.config` field.
### File connector
| name | description                |
|------|----------------------------|
| path | REQUIRED. Path to the file |


### Folder connector
| name    | type              | description                                                  |
|---------|-------------------|--------------------------------------------------------------|
| path    | str               | REQUIRED. Path to the folder                                 |
| trigger | ADD \| DEL \| ANY | DEFAULT ANY. Triggering mode: on added/removed files         |
| show    | LIST \| COUNT     | DEFAULT COUNT. Show changes as number of files or files list |

### SQL connector
| name     | description                                                                             |
|----------|-----------------------------------------------------------------------------------------|
| engine   | REQUIRED. Using module for connection. Must be installed in environment                 |
| server   | REQUIRED. Server IP or Host                                                             |
| database | REQUIRED. Database name                                                                 |
| table    | REQUIRED. Table or view name including schema                                           |
| order    | REQUIRED. The field by which the sorting takes place. Case-sensitive. Must be DATETIME. |
| charset  | Set table charset                                                                       |
