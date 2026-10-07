<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_value;

/**
 * Kiem tra ten dang nhap va mat khau Moodle cua mot nguoi dung.
 *
 * Web service chuan khong co ham nao chung minh duoc mat khau (login/token.php lai tao token va doi
 * hoi quyen moodle/webservice:createtoken), nen ham nay goi authenticate_user_login(), cung ham ma
 * trang dang nhap cua Moodle dung: dem lan dang nhap sai cho co che khoa cua Moodle, tu choi tai
 * khoan bi dinh chi, nhung khong sua nguoi dung va khong tao phien hay token. Mat khau chi duoc dung
 * de kiem tra, khong ghi lai o dau.
 */
class verify_login extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'username' => new external_value(PARAM_RAW, 'Ten dang nhap Moodle'),
            'password' => new external_value(PARAM_RAW, 'Mat khau Moodle'),
        ]);
    }

    public static function execute_returns(): external_value {
        return new external_value(PARAM_INT, 'Ma nguoi dung neu dung, 0 neu ten dang nhap hoac mat khau sai');
    }

    public static function execute(string $username, string $password): int {
        $params = self::validate_parameters(self::execute_parameters(), [
            'username' => $username,
            'password' => $password,
        ]);

        $failurereason = null;
        $user = authenticate_user_login(trim($params['username']), $params['password'], false, $failurereason, false);
        return $user ? (int) $user->id : 0;
    }
}
