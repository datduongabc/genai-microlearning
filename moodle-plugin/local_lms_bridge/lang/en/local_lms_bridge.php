<?php
defined('MOODLE_INTERNAL') || die();

$string['pluginname'] = 'LMS Bridge';
$string['privacy:metadata'] = 'LMS Bridge stores Moodle account confirmation requests with the requesting app account name and email.';

$string['invaliduser'] = 'No single active Moodle account matches this email.';
$string['invalidrequest'] = 'This request is unavailable, expired, or belongs to another account.';
$string['linksubject'] = 'Confirm Microlearning account link';
$string['linkmessage'] = 'App account {$a->name} ({$a->email}) wants to link to your Moodle account. Confirm only if this app account belongs to you. The request expires after one hour.';
$string['confirm'] = 'Confirm link';
$string['reject'] = 'Reject link';
$string['status_confirmed'] = 'Link confirmed. Return to the app to finish linking.';
$string['status_rejected'] = 'Link rejected.';
$string['status_expired'] = 'Request expired. Start a new request in the app.';
$string['messageprovider:accountlink'] = 'Account link confirmation';
