<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;

class request_link extends external_api {
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'app_account' => new external_value(PARAM_TEXT, 'app_account'),
            'email' => new external_value(PARAM_EMAIL, 'email'),
            'app_email' => new external_value(PARAM_EMAIL, 'app_email'),
            'app_name' => new external_value(PARAM_TEXT, 'app_name'),
        ]);
    }

    public static function execute(string $app_account, string $email, string $app_email, string $app_name): array {
        self::validate_context(\context_system::instance());
        require_capability('moodle/site:config', \context_system::instance());
        $params = self::validate_parameters(self::execute_parameters(), compact('app_account', 'email', 'app_email', 'app_name'));
        return \local_lms_bridge\link_requests::create($params['app_account'], $params['email'], $params['app_email'], $params['app_name']);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'request_id' => new external_value(PARAM_ALPHANUM, 'request_id'),
            'status' => new external_value(PARAM_TEXT, 'status'),
        ]);
    }
}
