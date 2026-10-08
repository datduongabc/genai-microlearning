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
}
