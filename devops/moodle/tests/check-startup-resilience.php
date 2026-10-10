<?php
// Integration check for a private Moodle only: it creates and removes its own fixtures and
// temporarily uninstalls the Vietnamese language pack. Pass the path of the setup script
// under test, or omit it to check the one installed in the image.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/course/lib.php');

\core\session\manager::set_user(get_admin());

$script = $argv[1] ?? '/usr/local/bin/configure-course-categories.php';
$run = function () use ($script) {
    exec('php ' . escapeshellarg($script) . ' 2>&1', $output, $exit);
    $output = implode("\n", $output);
    if ($exit !== 0) {
        throw new RuntimeException("Setup exited with {$exit}: {$output}");
    }
    return $output;
};
$idnumbers = ['dai-cuong', 'co-so-nganh', 'chuyen-nganh'];
$kept = function () use ($DB, $idnumbers) {
    [$insql, $params] = $DB->get_in_or_equal($idnumbers);
    return $DB->get_records_select_menu('course_categories', "idnumber {$insql}", $params, 'idnumber', 'idnumber,id');
};

$before = $run();
$ids = $kept();
if (count($ids) !== 3) {
    throw new RuntimeException('Setup did not create the three categories: ' . $before);
}

$proxy = [get_config('core', 'proxyhost'), get_config('core', 'proxyport')];
$extra = \core_course_category::create(['name' => 'Resilience fixture', 'idnumber' => 'resilience-fixture']);
$course = create_course((object) [
    'fullname' => 'Resilience fixture',
    'shortname' => 'resilience-fixture',
    'category' => $extra->id,
]);
try {
    $output = $run();
    if (!$DB->record_exists('course_categories', ['id' => $extra->id])
            || $DB->get_field('course', 'category', ['id' => $course->id]) != $extra->id) {
        throw new RuntimeException('Occupied additional category was changed: ' . $output);
    }
    if (str_contains($output, 'WARNING')) {
        throw new RuntimeException('Additional category caused a warning: ' . $output);
    }

    if (!(new \tool_langimport\controller())->uninstall_language('vi')) {
        throw new RuntimeException('Could not uninstall the Vietnamese language pack for the check');
    }
    get_string_manager()->reset_caches();
    set_config('proxyhost', '127.0.0.1');
    set_config('proxyport', 9);
    \core_course_category::get($ids['co-so-nganh'], MUST_EXIST, true)->update(['name' => 'Stale name']);
    $output = $run();
    get_string_manager()->reset_caches();
    if (get_string_manager()->translation_exists('vi')) {
        throw new RuntimeException('Language pack download was expected to fail');
    }
    if (!str_contains($output, 'WARNING')) {
        throw new RuntimeException('Failed language pack download was not reported: ' . $output);
    }
    $name = $DB->get_field('course_categories', 'name', ['id' => $ids['co-so-nganh']]);
    if (!str_contains($name, 'Cơ sở ngành') || !str_contains($name, 'Foundation Courses')) {
        throw new RuntimeException('Category was not configured without the language pack: ' . $name);
    }
    if ($kept() != $ids) {
        throw new RuntimeException('Category IDs changed without the language pack');
    }

    set_config('proxyhost', $proxy[0]);
    set_config('proxyport', $proxy[1]);
    $output = $run();
    get_string_manager()->reset_caches();
    if (!get_string_manager()->translation_exists('vi')) {
        throw new RuntimeException('Language pack was not installed on the next run: ' . $output);
    }
} finally {
    set_config('proxyhost', $proxy[0]);
    set_config('proxyport', $proxy[1]);
    delete_course($course->id, false);
    \core_course_category::get($extra->id, MUST_EXIST, true)->delete_full(false);
}
echo "Startup resilience checks passed\n";
