<?php
// Configures the e2e Moodle as an admin would in the UI: a teacher, a student, a course, an
// LTI 1.3 tool pointing at the local backend, and an activity using it. Prints JSON.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/user/lib.php');
require_once($CFG->dirroot . '/mod/lti/locallib.php');
require_once($CFG->libdir . '/enrollib.php');

$tool = getenv('TOOL_URL') ?: 'http://localhost:58000';
\core\session\manager::set_user(get_admin());

function e2e_user($username, $first) {
    global $DB, $CFG;
    if ($u = $DB->get_record('user', ['username' => $username])) {
        return $u;
    }
    $id = user_create_user((object)[
        'username' => $username, 'password' => 'E2e-user-pass-1!', 'firstname' => $first, 'lastname' => 'E2E',
        'email' => "$username@example.com", 'auth' => 'manual', 'confirmed' => 1, 'mnethostid' => $CFG->mnet_localhost_id,
    ]);
    return $DB->get_record('user', ['id' => $id]);
}

$teacher = e2e_user('e2eteacher', 'Teacher');
$student = e2e_user('e2estudent', 'Student');

$course = $DB->get_record('course', ['shortname' => 'HDH-E2E']) ?: create_course((object)[
    'fullname' => 'Hệ điều hành E2E', 'shortname' => 'HDH-E2E', 'category' => 1,
]);
enrol_try_internal_enrol($course->id, $teacher->id, $DB->get_field('role', 'id', ['shortname' => 'editingteacher']));
enrol_try_internal_enrol($course->id, $student->id, $DB->get_field('role', 'id', ['shortname' => 'student']));

$type = $DB->get_record('lti_types', ['name' => 'Microlearning E2E']);
if (!$type) {
    $config = (object)[
        'lti_typename' => 'Microlearning E2E',
        'lti_toolurl' => "$tool/api/lti/launch",
        'lti_ltiversion' => LTI_VERSION_1P3,
        'lti_keytype' => LTI_JWK_KEYSET,
        'lti_publickeyset' => "$tool/api/lti/jwks",
        'lti_initiatelogin' => "$tool/api/lti/login",
        'lti_redirectionuris' => "$tool/api/lti/launch",
        'lti_sendname' => LTI_SETTING_ALWAYS,
        'lti_sendemailaddr' => LTI_SETTING_ALWAYS,
        'lti_launchcontainer' => LTI_LAUNCH_CONTAINER_WINDOW,
        'lti_coursevisible' => LTI_COURSEVISIBLE_ACTIVITYCHOOSER,
        'lti_acceptgrades' => LTI_SETTING_NEVER,
        'lti_contentitem' => 0,
    ];
    $typeid = lti_add_type((object)['state' => LTI_TOOL_STATE_CONFIGURED], $config);
    $type = $DB->get_record('lti_types', ['id' => $typeid]);
}

$cm = $DB->get_record_sql("SELECT cm.id FROM {course_modules} cm JOIN {modules} m ON m.id = cm.module
                           WHERE m.name = 'lti' AND cm.course = ?", [$course->id]);
if (!$cm) {
    $info = create_module((object)[
        'modulename' => 'lti', 'course' => $course->id, 'section' => 0, 'visible' => 1,
        'name' => 'Microlearning', 'typeid' => $type->id, 'toolurl' => '', 'securetoolurl' => '',
        'launchcontainer' => LTI_LAUNCH_CONTAINER_WINDOW, 'instructorchoicesendname' => 1,
        'instructorchoicesendemailaddr' => 1, 'instructorchoiceacceptgrades' => 0, 'grade' => 0,
        'introeditor' => ['text' => '', 'format' => FORMAT_HTML, 'itemid' => 0],
    ]);
    $cm = (object)['id' => $info->coursemodule];
}

echo json_encode([
    'issuer' => $CFG->wwwroot,
    'client_id' => $type->clientid,
    'deployment_id' => (string)$type->id,
    'auth_login_url' => "$CFG->wwwroot/mod/lti/auth.php",
    'jwks_url' => "$CFG->wwwroot/mod/lti/certs.php",
    'course_id' => (int)$course->id,
    'cmid' => (int)$cm->id,
    'teacher_id' => (int)$teacher->id,
    'student_id' => (int)$student->id,
], JSON_UNESCAPED_SLASHES), "\n";
