<?php
namespace local_lms_bridge;

defined('MOODLE_INTERNAL') || die();

/** Consent belongs to the signed-in Moodle user, never the caller supplying an email. */
final class link_requests {
    public static function create(string $appaccount, string $email, string $appemail, string $appname): array {
        global $DB, $USER;
        $users = $DB->get_records_sql('SELECT id FROM {user} WHERE LOWER(email) = :email AND deleted = 0 AND suspended = 0',
            ['email' => strtolower(trim($email))]);
        if (count($users) !== 1) {
            throw new \moodle_exception('invaliduser', 'local_lms_bridge');
        }
        $request = (object) [
            'requestid' => bin2hex(random_bytes(32)), 'integrationuserid' => $USER->id,
            'appaccount' => $appaccount, 'userid' => reset($users)->id,
            'appemail' => $appemail, 'appname' => $appname, 'status' => 'pending',
            'timecreated' => time(), 'timemodified' => time(),
        ];
        $DB->insert_record('local_lms_bridge_requests', $request);
        $url = new \moodle_url('/local/lms_bridge/confirm.php', ['request' => $request->requestid]);
        $message = new \core\message\message();
        $message->component = 'local_lms_bridge';
        $message->name = 'accountlink';
        $message->userfrom = \core_user::get_noreply_user();
        $message->userto = $request->userid;
        $message->subject = get_string('linksubject', 'local_lms_bridge');
        $message->fullmessage = get_string('linkmessage', 'local_lms_bridge', (object) ['name' => $appname, 'email' => $appemail]);
        $message->fullmessageformat = FORMAT_PLAIN;
        $message->fullmessagehtml = '';
        $message->smallmessage = $message->fullmessage;
        $message->notification = 1;
        $message->contexturl = $url->out(false);
        $message->contexturlname = $message->subject;
        message_send($message);
        return ['request_id' => $request->requestid, 'status' => 'pending'];
    }

    private static function get(string $requestid): \stdClass {
        global $DB;
        $request = $DB->get_record('local_lms_bridge_requests', ['requestid' => $requestid]);
        if (!$request) {
            throw new \moodle_exception('invalidrequest', 'local_lms_bridge');
        }
        if ($request->status === 'pending' && $request->timecreated + 3600 <= time()) {
            $request->status = 'expired';
            $request->timemodified = time();
            $DB->update_record('local_lms_bridge_requests', $request);
        }
        return $request;
    }

    public static function status(string $requestid, string $appaccount): array {
        global $USER, $DB;
        $request = self::get($requestid);
        if ($request->integrationuserid != $USER->id || $request->appaccount !== $appaccount) {
            throw new \moodle_exception('invalidrequest', 'local_lms_bridge');
        }
        $active = $DB->record_exists('user', ['id' => $request->userid, 'deleted' => 0, 'suspended' => 0]);
        return ['status' => $active ? $request->status : 'rejected',
            'moodle_user_id' => $active && $request->status === 'confirmed' ? (int) $request->userid : 0];
    }

    public static function for_current_user(string $requestid): \stdClass {
        global $USER, $DB;
        $request = self::get($requestid);
        if ($request->userid != $USER->id || !$DB->record_exists('user', ['id' => $USER->id, 'deleted' => 0, 'suspended' => 0])) {
            throw new \moodle_exception('invalidrequest', 'local_lms_bridge');
        }
        return $request;
    }

    public static function confirm(string $requestid, bool $accept): string {
        global $DB;
        $request = self::for_current_user($requestid);
        if ($request->status !== 'pending') {
            throw new \moodle_exception('invalidrequest', 'local_lms_bridge');
        }
        $status = $accept ? 'confirmed' : 'rejected';
        // Only the first decision wins, even if both buttons are submitted concurrently.
        $DB->set_field_select('local_lms_bridge_requests', 'status', $status,
            'id = :id AND status = :pending', ['id' => $request->id, 'pending' => 'pending']);
        return $DB->get_field('local_lms_bridge_requests', 'status', ['id' => $request->id], MUST_EXIST);
    }
}
