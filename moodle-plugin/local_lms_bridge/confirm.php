<?php
require_once(__DIR__ . '/../../config.php');

require_login();
$requestid = required_param('request', PARAM_ALPHANUM);
$request = \local_lms_bridge\link_requests::for_current_user($requestid);
$url = new moodle_url('/local/lms_bridge/confirm.php', ['request' => $requestid]);
$PAGE->set_url($url);
$PAGE->set_context(context_system::instance());
$PAGE->set_title(get_string('linksubject', 'local_lms_bridge'));
$PAGE->set_heading(get_string('linksubject', 'local_lms_bridge'));

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_sesskey();
    $accept = required_param('accept', PARAM_BOOL);
    $request->status = \local_lms_bridge\link_requests::confirm($requestid, $accept);
}
echo $OUTPUT->header();
echo $OUTPUT->heading(get_string('linksubject', 'local_lms_bridge'));
echo html_writer::tag('p', s(get_string('linkmessage', 'local_lms_bridge',
    (object) ['name' => $request->appname, 'email' => $request->appemail])));
if ($request->status === 'pending') {
    foreach ([1 => 'confirm', 0 => 'reject'] as $accept => $label) {
        echo $OUTPUT->single_button(new moodle_url($url, ['accept' => $accept]),
            get_string($label, 'local_lms_bridge'), 'post');
    }
} else {
    echo $OUTPUT->notification(get_string('status_' . $request->status, 'local_lms_bridge'),
        $request->status === 'confirmed' ? 'success' : 'info');
}
echo $OUTPUT->footer();
