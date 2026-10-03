<?php
// Dinh nghia hai ham tu viet, va danh sach day du cac ham ma backend duoc phep
// goi qua token cua service nay.
//
// Danh sach 'functions' phai khop voi nhung ham backend thuc su goi trong
// backend/app/moodle/client.py. Khong co test tu dong so sanh hai phia: mot ham
// thieu o day chi that bai khi chay that, khong lo ra trong cac test da thay the
// Moodle bang ban gia.

defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_lms_bridge_create_book' => [
        'classname'    => 'local_lms_bridge\external\create_book',
        'methodname'   => 'execute',
        'description'  => 'Tao mot Book gom cac chuong ngan trong mot khoa hoc.',
        'type'         => 'write',
        'capabilities' => 'moodle/course:manageactivities',
    ],
    'local_lms_bridge_create_quiz' => [
        'classname'    => 'local_lms_bridge\external\create_quiz',
        'methodname'   => 'execute',
        'description'  => 'Tao mot Quiz gom cac cau hoi nhieu lua chon.',
        'type'         => 'write',
        'capabilities' => 'mod/quiz:addinstance',
    ],
];

$services = [
    'LMS Bridge service' => [
        'functions' => [
            'local_lms_bridge_create_book',
            'local_lms_bridge_create_quiz',
            'core_course_create_courses',
            'core_course_get_courses',
            'core_course_search_courses',
            'core_course_get_contents',
            'core_webservice_get_site_info',
            'enrol_manual_enrol_users',
            'core_user_get_users_by_field',
        ],
        'restrictedusers' => 0,
        'enabled'         => 1,
        'shortname'       => 'lms_bridge',
        'downloadfiles'   => 0,
        'uploadfiles'     => 0,
    ],
];
