<?php
// Connects to Postgres with exactly the settings in config.php and prints why it fails.
//
// Moodle's own error for every kind of connection failure is "Database connection failed",
// which says nothing about the cause. A wrong password, a missing database and a host that
// is down all look the same, and a rotated owner password that config.php still held the
// old value of took a long time to find because of it. This asks Postgres directly.
//
// Usage: php check-database.php [path/to/config.php]    exit 0 = connected, 1 = not.

define('CLI_SCRIPT', true);
// Load config.php and stop there, before Moodle opens its own connection and dies with
// the generic message this script exists to replace.
define('ABORT_AFTER_CONFIG', true);
require($argv[1] ?? '/var/www/html/config.php');

$port = (int)($CFG->dboptions['dbport'] ?? 0) ?: 5432;
$conninfo = sprintf(
    "host='%s' port=%d dbname='%s' user='%s' password='%s' connect_timeout=5",
    $CFG->dbhost, $port, $CFG->dbname, $CFG->dbuser, $CFG->dbpass
);

$connection = @pg_connect($conninfo);
if ($connection !== false) {
    echo "[check-database] connected to '{$CFG->dbname}' on {$CFG->dbhost}:{$port} as '{$CFG->dbuser}'\n";
    exit(0);
}

$reason = error_get_last()['message'] ?? 'unknown reason';
// Postgres does not echo the password back, but the connection string is ours: make sure
// no part of it that holds the password can end up in the log.
$reason = str_replace($CFG->dbpass, '***', $reason);
fwrite(STDERR, "[check-database] cannot connect to '{$CFG->dbname}' on {$CFG->dbhost}:{$port} as '{$CFG->dbuser}': {$reason}\n");
fwrite(STDERR, "[check-database] if the password was refused, compare \$CFG->dbpass in config.php with the password of role '{$CFG->dbuser}'\n");
exit(1);
