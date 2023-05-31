CREATE SCHEMA TRACKER

DROP TABLE IF EXISTS TRACKER.parameter
CREATE TABLE TRACKER.parameter (
    identifier  varchar(50)     NOT NULL,
    argument    varchar(max)    NOT NULL
)
INSERT INTO TRACKER.parameter VALUES
    ('TOKEN', 'put your API token here'),
    ('REQUEST_MAXTIME', '43200'),
    ('READ_TIMEOUT', '150'),
    ('CHANNELS_PER_PAGE', '5'),
    ('DELAY', '2.5'),
    ('ACTUALIZE_INTERVAL', '00:00'),
    ('SILENT_ACTUALIZE', 'False'),
    -- MESSAGES
    ('NOT_ALLOWED', 'Your permission level ({flag}) is insufficient to perform this operation.'),
    ('ACCESS_REQUESTED', 'You have requested access. Your request is valid until {maxtime}. If you have not received a response after this time, contact the administrator.'),
    ('ACCESS_REQUEST_MESSAGE', 'User {username} requested access. This request will be automatically rejected on {maxtime}'),
    ('ALREADY_ACCESSIBLE', 'You already have access ({flag}).'),
    ('REQUEST_APPROVED', 'Request was approved by {username}.'),
    ('REQUEST_REJECTED', 'Request was rejected by {username}.'),
    ('SUBSCRIPTIONS_MENU_HEADER', 'Currently available channels ({page}/{total})'),
    ('WRONG_ARGUMENT', 'Argument you passed has the wrong format.'),
    ('NOTIFICATION_DISABLED', 'Notifications are disabled until {sleeptime}.'),
    ('NOTIFICATION_ENABLED', 'Notifications are enabled again.'),
    ('SUSPEND_SUBSCRIPTION', 'Channel {name} was disabled. Your subscription has been suspended.'),
    ('RESUME_SUBSCRIPTION', 'Channel {name} was enabled. Your subscription has been renewed.'),
    ('CHECK_REPLY', 'Forcing listeners...'),
    ('ACTUALIZE_REPLY', 'Connectors configuration reloaded.'),
    ('SHUTDOWN_REPLY', 'Shutdown job was scheduled. See ya!')


DROP TABLE IF EXISTS TRACKER.permission
CREATE TABLE TRACKER.permission (
    user_id     bigint      NOT NULL UNIQUE,    -- telegram user id
    flag        tinyint     NOT NULL            -- permission level flag; use 0 to block user
)


DROP TABLE IF EXISTS TRACKER.channel
CREATE TABLE TRACKER.channel (
    channel_id  int             NOT NULL IDENTITY(1,1),     -- in-code identifier
    identifier  varchar(50)     NOT NULL UNIQUE,            -- channel visible name
    connector   varchar(50)     NOT NULL,                   -- connector type
    config      varchar(max)    NOT NULL,                   -- connector destination (table or file)
    polling     varchar(8)      NOT NULL,                   -- polling interval or fixed time
    active      bit             NOT NULL DEFAULT 1
)


DROP TABLE IF EXISTS TRACKER.subscription
CREATE TABLE TRACKER.subscription (
    user_id     bigint          NOT NULL,
    channel_id  bigint          NOT NULL,
    active      bit             NOT NULL DEFAULT 1
)
