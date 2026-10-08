<?php
namespace local_lms_bridge;

use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(course_access::class)]
final class teacher_courses_test extends \advanced_testcase {
    public function test_returns_only_courses_with_live_manageactivities_capability(): void {
        global $DB;
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user();
        $teacher = $this->getDataGenerator()->create_course();
        $student = $this->getDataGenerator()->create_course();
        $teacherrole = $DB->get_field('role', 'id', ['shortname' => 'editingteacher']);
        $studentrole = $DB->get_field('role', 'id', ['shortname' => 'student']);
        $this->getDataGenerator()->enrol_user($user->id, $teacher->id, $teacherrole);
        $this->getDataGenerator()->enrol_user($user->id, $student->id, $studentrole);
        $result = course_access::teacher_courses($user->id);
        $this->assertTrue($result['exists']);
        $this->assertSame($user->email, $result['email']);
        $this->assertEquals([$teacher->id], array_column($result['courses'], 'id'));
        assign_capability('moodle/course:manageactivities', CAP_PROHIBIT, $teacherrole,
            \context_course::instance($teacher->id)->id);
        $this->assertSame([], course_access::teacher_courses($user->id)['courses']);
    }

    public function test_nonstandard_role_with_capability_qualifies_without_enrollment_mutation(): void {
        global $DB;
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user();
        $course = $this->getDataGenerator()->create_course();
        $roleid = create_role('Course author', 'customauthor', '');
        $context = \context_course::instance($course->id);
        assign_capability('moodle/course:manageactivities', CAP_ALLOW, $roleid, $context->id);
        role_assign($roleid, $user->id, $context->id);
        $before = $DB->count_records('user_enrolments');
        $this->assertEquals([$course->id], array_column(course_access::teacher_courses($user->id)['courses'], 'id'));
        $this->assertSame($before, $DB->count_records('user_enrolments'));
    }

    public function test_inactive_or_unknown_identity_has_no_permissions(): void {
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user(['suspended' => 1]);
        foreach ([$user->id, -1] as $id) {
            $result = course_access::teacher_courses($id);
            $this->assertFalse($result['exists']);
            $this->assertSame([], $result['courses']);
        }
    }
}
