<?php
namespace local_lms_bridge;

defined('MOODLE_INTERNAL') || die();

/** Course access comes from the linked user's current capability, not role names. */
final class course_access {
    public static function teacher_courses(int $userid): array {
        global $DB;
        $user = $DB->get_record('user', ['id' => $userid, 'deleted' => 0, 'suspended' => 0], 'id,email');
        if (!$user) {
            return ['exists' => false, 'email' => '', 'courses' => []];
        }
        $courses = get_user_capability_course('moodle/course:manageactivities', $userid, true, 'fullname,shortname') ?: [];
        $result = [];
        foreach ($courses as $course) {
            if ($course->id == SITEID) {
                continue;
            }
            $result[] = ['id' => (int) $course->id, 'fullname' => $course->fullname, 'shortname' => $course->shortname];
        }
        return ['exists' => true, 'email' => $user->email, 'courses' => $result];
    }

    public static function require_author(int $userid, \context_course $context): void {
        global $DB;
        if (!$DB->record_exists('user', ['id' => $userid, 'deleted' => 0, 'suspended' => 0])) {
            throw new \moodle_exception('invaliduser', 'local_lms_bridge');
        }
        require_capability('moodle/course:manageactivities', $context, $userid);
    }
}
