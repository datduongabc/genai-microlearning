<?php
defined('MOODLE_INTERNAL') || die();

$messageproviders = ['accountlink' => ['defaults' => [
    'popup' => MESSAGE_PERMITTED + MESSAGE_DEFAULT_ENABLED,
    'email' => MESSAGE_DISALLOWED,
]]];
