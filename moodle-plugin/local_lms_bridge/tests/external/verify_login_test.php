<?php
namespace local_lms_bridge\external;

use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(verify_login::class)]
final class verify_login_test extends \advanced_testcase {
    public function test_right_username_and_password_return_the_user_id(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $user = $this->getDataGenerator()->create_user(['username' => 'lan.nguyen', 'password' => 'Aa1!right-pass']);

        $this->assertSame((int) $user->id, verify_login::execute('lan.nguyen', 'Aa1!right-pass'));
    }

    public function test_the_username_is_trimmed_and_not_case_sensitive(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $user = $this->getDataGenerator()->create_user(['username' => 'lan.nguyen', 'password' => 'Aa1!right-pass']);

        $this->assertSame((int) $user->id, verify_login::execute('  Lan.Nguyen ', 'Aa1!right-pass'));
    }

    public function test_wrong_password_unknown_user_and_empty_password_return_zero(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->getDataGenerator()->create_user(['username' => 'lan.nguyen', 'password' => 'Aa1!right-pass']);

        $this->assertSame(0, verify_login::execute('lan.nguyen', 'Aa1!wrong-pass'));
        $this->assertSame(0, verify_login::execute('nobody.here', 'Aa1!right-pass'));
        $this->assertSame(0, verify_login::execute('lan.nguyen', ''));
    }

    public function test_a_suspended_user_is_refused_even_with_the_right_password(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->getDataGenerator()->create_user([
            'username' => 'lan.nguyen', 'password' => 'Aa1!right-pass', 'suspended' => 1,
        ]);

        $this->assertSame(0, verify_login::execute('lan.nguyen', 'Aa1!right-pass'));
    }

    public function test_checking_a_password_does_not_change_the_user(): void {
        global $DB;
        $this->resetAfterTest();
        $this->setAdminUser();
        $user = $this->getDataGenerator()->create_user(['username' => 'lan.nguyen', 'password' => 'Aa1!right-pass']);
        $before = $DB->get_record('user', ['id' => $user->id], '*', MUST_EXIST);

        verify_login::execute('lan.nguyen', 'Aa1!right-pass');

        $after = $DB->get_record('user', ['id' => $user->id], '*', MUST_EXIST);
        $this->assertEquals($before, $after);
    }
}
