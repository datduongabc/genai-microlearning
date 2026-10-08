<?php
namespace local_lms_bridge;

use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(external\create_book::class)]
#[CoversClass(external\create_quiz::class)]
final class actor_permission_test extends \advanced_testcase {
    public function test_service_admin_cannot_write_on_behalf_of_a_student(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $user = $this->getDataGenerator()->create_user();
        $course = $this->getDataGenerator()->create_course();
        $this->expectException(\required_capability_exception::class);
        external\create_book::execute($course->id, 'Denied', [], $user->id);
    }

    public function test_real_webservice_dispatch_keeps_actor_and_activity_parameters_in_order(): void {
        global $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course();
        $_POST['sesskey'] = sesskey();
        try {
            foreach (['book', 'quiz'] as $type) {
                $params = ['courseid' => $course->id, 'name' => 'WS ' . $type, 'actorid' => $USER->id];
                if ($type === 'book') {
                    $params['chapters'] = [['title' => 'Chapter', 'content' => '<p>Lesson</p>', 'subchapter' => 0]];
                } else {
                    $params['questions'] = [];
                }
                $result = \core_external\external_api::call_external_function('local_lms_bridge_create_' . $type, $params);
                $this->assertFalse($result['error']);
                $this->assertGreaterThan(0, $result['data']);
            }
        } finally {
            unset($_POST['sesskey']);
        }
    }
}
