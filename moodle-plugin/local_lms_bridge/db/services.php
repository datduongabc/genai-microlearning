<?php
// Account consent, live course capability lookup and activity creation.
// Core course lookup is restricted to exact IDs by the backend error verifier.

defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_lms_bridge_teacher_courses' => [
        'classname' => 'local_lms_bridge\external\teacher_courses',
        'methodname' => 'execute',
        'description' => 'Courses the linked Moodle user can manage',
        'type' => 'read',
        'capabilities' => 'moodle/site:config',
    ],

    'local_lms_bridge_request_link' => [
        'classname' => 'local_lms_bridge\external\request_link',
        'methodname' => 'execute',
        'description' => 'Moodle account confirmation request_link',
        'type' => 'write',
        'capabilities' => 'moodle/site:config',
    ],
    'local_lms_bridge_link_status' => [
        'classname' => 'local_lms_bridge\external\link_status',
        'methodname' => 'execute',
        'description' => 'Moodle account confirmation link_status',
        'type' => 'write',
        'capabilities' => 'moodle/site:config',
    ],

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
            'local_lms_bridge_teacher_courses',
            'local_lms_bridge_request_link',
            'local_lms_bridge_link_status',
            'local_lms_bridge_create_book',
            'local_lms_bridge_create_quiz',
            'core_course_get_courses',
            'core_course_get_contents',
        ],
        'restrictedusers' => 0,
        'enabled'         => 1,
        'shortname'       => 'lms_bridge',
        'downloadfiles'   => 0,
        'uploadfiles'     => 0,
    ],
];
