<?php
namespace local_lms_bridge;

use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(link_requests::class)]
final class link_requests_test extends \advanced_testcase {
    private function request_for($user): array {
        $this->setAdminUser();
        return link_requests::create('app-account-1', strtoupper($user->email), 'local@example.com', 'Local User');
    }

    public function test_request_remains_pending_until_its_signed_in_owner_confirms(): void {
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user(['email' => 'person@example.com']);
        $request = $this->request_for($user);
        $this->assertSame('pending', link_requests::status($request['request_id'], 'app-account-1')['status']);
        $this->setUser($user);
        $this->assertSame('confirmed', link_requests::confirm($request['request_id'], true));
        $this->setAdminUser();
        $status = link_requests::status($request['request_id'], 'app-account-1');
        $this->assertSame('confirmed', $status['status']);
        $this->assertEquals($user->id, $status['moodle_user_id']);
    }

    public function test_another_moodle_user_cannot_confirm_the_request(): void {
        $this->resetAfterTest();
        $owner = $this->getDataGenerator()->create_user();
        $request = $this->request_for($owner);
        $this->setUser($this->getDataGenerator()->create_user());
        $this->expectException(\moodle_exception::class);
        link_requests::confirm($request['request_id'], true);
    }

    public function test_owner_can_reject_and_no_identity_is_returned(): void {
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user();
        $request = $this->request_for($user);
        $this->setUser($user);
        $this->assertSame('rejected', link_requests::confirm($request['request_id'], false));
        $this->setAdminUser();
        $result = link_requests::status($request['request_id'], 'app-account-1');
        $this->assertSame('rejected', $result['status']);
        $this->assertEquals(0, $result['moodle_user_id']);
    }

    public function test_expired_request_cannot_be_confirmed(): void {
        global $DB;
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user();
        $request = $this->request_for($user);
        $DB->set_field('local_lms_bridge_requests', 'timecreated', time() - 7200, ['requestid' => $request['request_id']]);
        $this->assertSame('expired', link_requests::status($request['request_id'], 'app-account-1')['status']);
        $this->setUser($user);
        $this->expectException(\moodle_exception::class);
        link_requests::confirm($request['request_id'], true);
    }

    public function test_request_status_is_bound_to_the_app_account(): void {
        $this->resetAfterTest();
        $request = $this->request_for($this->getDataGenerator()->create_user());
        $this->expectException(\moodle_exception::class);
        link_requests::status($request['request_id'], 'someone-else');
    }

    public function test_suspended_user_cannot_confirm_a_request(): void {
        global $DB;
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user();
        $request = $this->request_for($user);
        $DB->set_field('user', 'suspended', 1, ['id' => $user->id]);
        $this->assertSame('pending', link_requests::status($request['request_id'], 'app-account-1')['status']);
        $this->setUser($user);
        $this->expectException(\moodle_exception::class);
        link_requests::confirm($request['request_id'], true);
    }

    public function test_unmatched_requests_are_indistinguishable_and_expire(): void {
        global $DB;
        $this->resetAfterTest();
        $this->setAdminUser();
        $user = $this->getDataGenerator()->create_user();
        $suspended = $this->getDataGenerator()->create_user(['suspended' => 1]);
        $first = $this->getDataGenerator()->create_user();
        $second = $this->getDataGenerator()->create_user();
        $DB->set_field('user', 'email', $first->email, ['id' => $second->id]);
        $emails = [$user->email, 'unknown@example.com', $suspended->email, $first->email];
        $usercount = $DB->count_records('user');
        $ids = [];
        foreach ($emails as $email) {
            $request = link_requests::create('app', $email, 'app@example.com', 'User');
            $this->assertSame(['request_id', 'status'], array_keys($request));
            $this->assertSame('pending', $request['status']);
            $this->assertMatchesRegularExpression('/^[a-f0-9]{64}$/', $request['request_id']);
            $ids[] = $request['request_id'];
            $this->assertSame(['status' => 'pending', 'moodle_user_id' => 0],
                link_requests::status($request['request_id'], 'app'));
            $DB->set_field('local_lms_bridge_requests', 'timecreated', time() - 3600,
                ['requestid' => $request['request_id']]);
            $this->assertSame(['status' => 'expired', 'moodle_user_id' => 0],
                link_requests::status($request['request_id'], 'app'));
        }
        $this->assertCount(4, array_unique($ids));
        $this->assertEquals($usercount, $DB->count_records('user'));
    }

    public function test_unmatched_request_cannot_be_confirmed_even_by_admin(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $request = link_requests::create('app', 'unknown@example.com', 'app@example.com', 'User');
        $this->expectException(\moodle_exception::class);
        link_requests::confirm($request['request_id'], true);
    }

    public function test_status_is_bound_to_the_integration_user_too(): void {
        $this->resetAfterTest();
        $request = $this->request_for($this->getDataGenerator()->create_user());
        $this->setUser($this->getDataGenerator()->create_user());
        $this->expectException(\moodle_exception::class);
        link_requests::status($request['request_id'], 'app-account-1');
    }
}
