<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

class teacher_courses extends external_api {
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters(['user_id' => new external_value(PARAM_INT, 'Linked Moodle user')]);
    }

    public static function execute(int $user_id): array {
        self::validate_context(\context_system::instance());
        require_capability('moodle/site:config', \context_system::instance());
        $params = self::validate_parameters(self::execute_parameters(), ['user_id' => $user_id]);
        return \local_lms_bridge\course_access::teacher_courses($params['user_id']);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'exists' => new external_value(PARAM_BOOL, 'Account not deleted'),
            'suspended' => new external_value(PARAM_BOOL, 'Account suspended, so it has no course access'),
            'email' => new external_value(PARAM_RAW, 'Moodle email, empty if deleted'),
            'courses' => new external_multiple_structure(new external_single_structure([
                'id' => new external_value(PARAM_INT, 'Course id'),
                'fullname' => new external_value(PARAM_TEXT, 'Full course name'),
                'shortname' => new external_value(PARAM_TEXT, 'Short course name'),
            ])),
        ]);
    }
}
