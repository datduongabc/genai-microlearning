<?php
// install_database.php runs its last steps in order: write the admin
// password/email, then apply all default settings. If the container is killed
// part-way, the tables exist so the entrypoint sees "already installed" and
// the site is left half-configured. Each step below detects its own symptom
// and is a no-op on a healthy site.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/adminlib.php');

// 1. Admin still has the web-installer placeholder password and no email:
// nobody can log in and web services fail with "User not fully set-up".
$password = getenv('MOODLE_ADMIN_PASSWORD');
$email = getenv('MOODLE_ADMIN_EMAIL');
$username = getenv('MOODLE_ADMIN_USER') ?: 'admin';
$admin = get_admin();
if ($admin->password === 'adminsetuppending') {
    if (!$password || !$email) {
        fwrite(STDERR, "MOODLE_ADMIN_PASSWORD and MOODLE_ADMIN_EMAIL must be set\n");
        exit(1);
    }
    $DB->set_field('user', 'password', hash_internal_user_password($password), ['id' => $admin->id]);
    $DB->set_field('user', 'email', $email, ['id' => $admin->id]);
    if ($username !== $admin->username && !$DB->record_exists('user', ['username' => $username])) {
        $DB->set_field('user', 'username', $username, ['id' => $admin->id]);
    }
    // admin/index.php would only ever clear this once the password is a real one.
    unset_config('adminsetuppending');
    echo "admin account finished from environment (user '{$username}')\n";
} else {
    echo "admin account already configured\n";
}

// 2. Default settings never applied. enrol_manual/defaultenrol is always
// written by a complete install; without it new courses get no enrolment
// method at all. Only unset settings are filled in, never overwritten.
if (get_config('enrol_manual', 'defaultenrol') === false) {
    \core\session\manager::set_user(get_admin());
    admin_apply_default_settings(null, false);
    echo "default settings applied\n";
} else {
    echo "default settings already present\n";
}
