<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;

class link_status extends external_api {
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'request_id' => new external_value(PARAM_ALPHANUM, 'request_id'),
            'app_account' => new external_value(PARAM_TEXT, 'app_account'),
        ]);
    }

    public static function execute(string $request_id, string $app_account): array {
        self::validate_context(\context_system::instance());
        require_capability('moodle/site:config', \context_system::instance());
        $params = self::validate_parameters(self::execute_parameters(), compact('request_id', 'app_account'));
        return \local_lms_bridge\link_requests::status($params['request_id'], $params['app_account']);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'status' => new external_value(PARAM_TEXT, 'status'),
            'moodle_user_id' => new external_value(PARAM_INT, 'moodle_user_id'),
        ]);
    }
}
