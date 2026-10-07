<?php
// Plugin cau noi giua he thong sinh Microlearning va Moodle.
//
// Ly do ton tai: mod_book va mod_quiz chi phoi bay ham web service dang DOC.
// Khong co ham chuan nao tao duoc mot Book hay mot Quiz moi, nen hai ham o
// classes/external/ goi thang vao API noi bo cua Moodle.

defined('MOODLE_INTERNAL') || die();

$plugin->component = 'local_lms_bridge';
// Moodle chi doc lai dinh nghia service khi so phien ban tang len. Tang so nay
// moi lan doi db/services.php, neu khong Moodle se giu ban cu trong cache.
$plugin->version   = 2026100700;
// Moodle 5.2, xem moodle/public/version.php.
$plugin->requires  = 2026042000;
$plugin->maturity  = MATURITY_STABLE;
$plugin->release   = '1.0.0';
