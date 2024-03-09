CREATE SCHEMA TRACKER

DROP TABLE IF EXISTS [TRACKER].[parameter]
CREATE TABLE [TRACKER].[parameter] (
    [identifier]    VARCHAR(50)     NOT NULL UNIQUE,
    [argument]      NVARCHAR(MAX)   NOT NULL
)
INSERT INTO [TRACKER].[parameter] VALUES
    ('LOGFILE', 'logs/tracker.log')
    ,('LOGLEVEL', 'DEBUG')
    ,('LOGSIZE', '1048576')
    ,('LOGBACKUP', '5')
    ,('READ_TIMEOUT', '60')
    ,('WRITE_TIMEOUT', '60')
    ,('CONNECT_TIMEOUT', '30')
    ,('POOL_TIMEOUT', '10')
    ,('UPDOWN_DELAY', '2.5')
    ,('POLLING', '600')
    ,('SILENT_ACTUALIZE', 'true')
    ,('TEXT_MAX_LENGTH', '4096')
    ,('ACCESS_REQUEST_MAXTIME', '43200')
    ,('ACTUALIZE_INTERVAL', '00:05')
    ,('CHANNELS_PER_PAGE', '5')
    ,('NOTIFICATION_LIFETIME', '150')
    ,('RETRY_INTERVAL', '1')
    ,('MESSAGE_NOT_ALLOWED', N'⛔ Your permission level ({flag}) is insufficient to perform this operation.')
    ,('MESSAGE_ALREADY_ACCESSIBLE', 'You already have access ({flag}).')
    ,('MESSAGE_ACCESS_REQUESTED', 'You have requested access. Your request is valid until {maxtime}. If you have not received a response after this time, contact the administrator.')
    ,('MESSAGE_ACCESS_REQUEST_TEXT', 'User {username} requested access. This request will be automatically rejected on {maxtime}.')
    ,('MESSAGE_REQUEST_APPROVED', 'Request was approved by {username}.')
    ,('MESSAGE_REQUEST_REJECTED', 'Request was rejected by {username}.')
    ,('MESSAGE_NOTIFICATION_DISABLED', 'Notifications are disabled until {sleeptime}.')
    ,('MESSAGE_NOTIFICATION_ENABLED', 'Notifications are enabled again.')
    ,('MESSAGE_RESUME_SUBSCRIPTION', 'Channel {name} was enabled. Your subscription has been renewed.')
    ,('MESSAGE_SUSPEND_SUBSCRIPTION', 'Channel {name} was disabled. Your subscription has been suspended.')
    ,('MESSAGE_CHECK_TEXT', 'Forcing listeners...')
    ,('MESSAGE_ACTUALIZE_TEXT', 'Connectors configuration reloaded.')
    ,('MESSAGE_SHUTDOWN_TEXT', N'Shutdown job was scheduled. See ya! 👋')
    ,('MESSAGE_WRONG_ARGUMENT', 'Argument you passed has the wrong format.')
    ,('MESSAGE_SQL_CONNECTION_LOST', N'❗❗❗ UFO has stolen your SQL connection [{name}] 👽💀👻😱')
    ,('MESSAGE_ASSERTION', N'I think i''m gonna throw up 🤢. Check my log please.')
    ,('MESSAGE_DONE', N'✔ done.')
    ,('MESSAGE_RELOAD_CONFIGURATION', N'✔ Configuration reloaded.' + CHAR(13) + CHAR(10) + 'Keep in mind that in order to apply the timeout settings, you should completely restart the bot.')
    ,('UI_SUBSCRIPTIONS_MENU_HEADER', 'Currently available channels ({page}/{total})')


DROP TABLE IF EXISTS [TRACKER].[permission]
CREATE TABLE [TRACKER].[permission] (
    [user_id]   BIGINT          NOT NULL UNIQUE,    -- telegram user id
    [username]  NVARCHAR(500)       NULL,           -- tlegram username
    [flag]      TINYINT         NOT NULL            -- permission level flag; use 0 to block user
)


DROP TABLE IF EXISTS [TRACKER].[channel]
CREATE TABLE [TRACKER].[channel] (
    [channel_id]    INT             NOT NULL IDENTITY(1,1) UNIQUE,  -- channel identifier
    [identifier]    VARCHAR(50)     NOT NULL UNIQUE,                -- channel printable name
    [connector]     VARCHAR(50)     NOT NULL,                       -- connector type
    [config]        VARCHAR(MAX)    NOT NULL,                       -- connector configuration
    [polling]       VARCHAR(8)      NOT NULL,                       -- polling interval or fixed time
    [active]        BIT             NOT NULL DEFAULT 1              -- channel is active flag
)


DROP TABLE IF EXISTS [TRACKER].[subscription]
CREATE TABLE [TRACKER].[subscription] (
    [user_id]       BIGINT  NOT NULL,
    [channel_id]    BIGINT  NOT NULL,
    [active]        BIT     NOT NULL DEFAULT 1
)
