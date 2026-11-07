<?php
// Takes a user out of the e2e course, as a teacher or admin would in the course's participants page.
//   php /e2e/unenrol.php e2estudent
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/enrollib.php');

$user = $DB->get_record('user', ['username' => $argv[1]], '*', MUST_EXIST);
$course = $DB->get_record('course', ['shortname' => 'HDH-E2E'], '*', MUST_EXIST);
$plugin = enrol_get_plugin('manual');
$instance = $DB->get_record('enrol', ['courseid' => $course->id, 'enrol' => 'manual'], '*', MUST_EXIST);
$plugin->unenrol_user($instance, $user->id);
echo "unenrolled {$user->username} from {$course->shortname}\n";
