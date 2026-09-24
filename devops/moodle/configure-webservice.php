<?php
// Switches web services on and makes sure the token the backend is configured with
// (MOODLE_TOKEN) exists for the admin account on the "LMS Bridge service". Run on every
// container start, after the plugin was installed or upgraded: a no-op on a site that is
// already set up, and what makes a brand new Moodle usable without anybody clicking
// through the admin pages.
//
// The token comes from the environment instead of being generated here, so the value the
// backend already has stays valid across a rebuilt Moodle. It is never printed.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/adminlib.php');
require_once($CFG->dirroot . '/webservice/lib.php');

// 1. Web services and the REST protocol.
set_config('enablewebservices', 1);
$protocols = array_values(array_filter(explode(',', (string) get_config('core', 'webserviceprotocols'))));
if (!in_array('rest', $protocols, true)) {
    $protocols[] = 'rest';
    set_config('webserviceprotocols', implode(',', $protocols));
    echo "REST protocol enabled\n";
}

// 2. The service that local_lms_bridge defines (db/services.php).
$service = $DB->get_record('external_services', ['shortname' => 'lms_bridge']);
if (!$service) {
    fwrite(STDERR, "The 'lms_bridge' web service does not exist: local_lms_bridge is not installed\n");
    exit(1);
}
if (!$service->enabled) {
    $DB->set_field('external_services', 'enabled', 1, ['id' => $service->id]);
}

// 3. The token.
$wanted = getenv('MOODLE_TOKEN');
if ($wanted === false || $wanted === '') {
    echo "MOODLE_TOKEN is not set: no web service token was created\n";
    exit(0);
}
if (!preg_match('/^[A-Za-z0-9]{32}$/', $wanted)) {
    fwrite(STDERR, "MOODLE_TOKEN must be 32 letters or digits, as Moodle's own tokens are\n");
    exit(1);
}
// MOODLE_TOKEN in .env is the only credential the backend uses on this service, so any other
// token on it is revoked. Rotating the token used to mean editing .env AND deleting the old
// token by hand in Moodle; forget the second step and a leaked token kept working.
$revoked = $DB->count_records_select(
    'external_tokens', 'externalserviceid = ? AND token <> ?', [$service->id, $wanted]
);
if ($revoked > 0) {
    $DB->delete_records_select('external_tokens', 'externalserviceid = ? AND token <> ?', [$service->id, $wanted]);
    echo "revoked {$revoked} old web service token(s)\n";
}
if ($DB->record_exists('external_tokens', ['token' => $wanted])) {
    echo "web service token already present\n";
    exit(0);
}
$admin = get_admin();
$generated = \core_external\util::generate_token(
    EXTERNAL_TOKEN_PERMANENT, $service, $admin->id, context_system::instance()
);
$DB->set_field('external_tokens', 'token', $wanted, ['token' => $generated]);
echo "web service token created for '{$admin->username}'\n";
