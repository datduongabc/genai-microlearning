<?php
/**
 * Viet lai config.php cho mot Moodle da cai dat tu truoc.
 *
 * Tinh huong that da gap: thu muc ma nguon Moodle bi doi ten va config.php bien
 * mat, trong khi co so du lieu van con nguyen bang du lieu. Luc do admin/cli/
 * install.php khong dung duoc vi no doi mot database trong, con Moodle thi
 * khong chay duoc vi thieu config.php.
 *
 * Script tu thoat 0 khi da viet xong config.php, va thoat 1 khi co so du lieu
 * chua co bang nao cua Moodle, de entrypoint biet la phai cai moi that su.
 *
 * Bi mat chi di qua bien moi truong roi vao thang config.php, khong bao gio
 * duoc in ra man hinh hay ghi vao log.
 */

$configpath = '/var/www/html/config.php';
$prefix = 'mdl_';

$host = getenv('MOODLE_DB_HOST') ?: 'db';
$port = getenv('MOODLE_DB_PORT') ?: '5432';
$name = getenv('MOODLE_DB_NAME');
$user = getenv('MOODLE_DB_USER');
$pass = getenv('MOODLE_DB_PASSWORD');
$wwwroot = rtrim(getenv('MOODLE_WWWROOT') ?: 'http://localhost:8081', '/');

$conn = @pg_connect("host={$host} port={$port} dbname={$name} user={$user} password={$pass}");
if ($conn === false) {
    fwrite(STDERR, "[restore-config] Khong ket noi duoc co so du lieu {$name} tren {$host}:{$port}.\n");
    exit(2);
}

$result = pg_query_params(
    $conn,
    'SELECT COUNT(*) FROM information_schema.tables WHERE table_name = $1',
    [$prefix . 'config']
);
$datacao = $result !== false && (int) pg_fetch_result($result, 0, 0) > 0;
pg_close($conn);

if (!$datacao) {
    fwrite(STDOUT, "[restore-config] Co so du lieu chua co bang Moodle nao, can cai moi.\n");
    exit(1);
}

$config = <<<PHP
<?php
// Duoc sinh lai boi devops/moodle/restore-config.php vi ban goc da mat trong khi
// co so du lieu van con du bang. Gia tri lay tu bien moi truong cua container.
unset(\$CFG);
global \$CFG;
\$CFG = new stdClass();

\$CFG->dbtype    = 'pgsql';
\$CFG->dblibrary = 'native';
\$CFG->dbhost    = '{$host}';
\$CFG->dbname    = '{$name}';
\$CFG->dbuser    = '{$user}';
\$CFG->dbpass    = '{$pass}';
\$CFG->prefix    = '{$prefix}';
\$CFG->dboptions = [
    'dbpersist' => 0,
    'dbport'    => '{$port}',
    'dbsocket'  => '',
];

\$CFG->wwwroot   = '{$wwwroot}';
\$CFG->dataroot  = '/var/www/moodledata';
\$CFG->admin     = 'admin';
\$CFG->directorypermissions = 0777;

require_once(__DIR__ . '/lib/setup.php');

PHP;

if (file_put_contents($configpath, $config) === false) {
    fwrite(STDERR, "[restore-config] Khong ghi duoc {$configpath}.\n");
    exit(2);
}
// Chi chu so huu doc duoc: tep nay chua mat khau co so du lieu.
chmod($configpath, 0640);
chown($configpath, 'www-data');

fwrite(STDOUT, "[restore-config] Da viet lai config.php cho Moodle da cai san.\n");
exit(0);
