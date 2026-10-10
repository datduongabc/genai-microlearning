<?php
// Run after upgrades on every startup; category IDs, existing courses and other categories are retained.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/filterlib.php');

\core\session\manager::set_user(get_admin());

$warnings = 0;
$warn = function (string $message) use (&$warnings) {
    $warnings++;
    fwrite(STDERR, "[configure-course-categories] WARNING: {$message}\n");
};

if (!get_string_manager()->translation_exists('vi')) {
    try {
        (new \tool_langimport\controller())->install_languagepacks(['vi']);
    } catch (Throwable $e) {
        $warn('Vietnamese language pack download failed: ' . $e->getMessage());
    }
    get_string_manager()->reset_caches();
    if (!get_string_manager()->translation_exists('vi')) {
        $warn('Vietnamese language pack is not installed; retrying on the next start');
    }
}

filter_set_global_state('multilang', TEXTFILTER_ON);
filter_set_applies_to_strings('multilang', true);

$names = [
    'dai-cuong' => ['Đại cương', 'General Education'],
    'co-so-nganh' => ['Cơ sở ngành', 'Foundation Courses'],
    'chuyen-nganh' => ['Chuyên ngành', 'Specialized Courses'],
];
foreach ($names as $idnumber => [$vi, $en]) {
    try {
        $record = $DB->get_record('course_categories', ['idnumber' => $idnumber]);
        if (!$record && $idnumber === 'dai-cuong') {
            $default = $DB->get_record('course_categories', ['id' => 1]);
            if ($default && $default->name === 'Category 1') {
                $record = $default;
            }
        }
        $data = [
            'name' => '<span lang="vi" class="multilang">' . $vi . '</span>'
                . '<span lang="en" class="multilang">' . $en . '</span>',
            'idnumber' => $idnumber,
            'parent' => 0,
            'visible' => 1,
        ];
        if ($record) {
            \core_course_category::get($record->id, MUST_EXIST, true)->update($data);
        } else {
            \core_course_category::create($data);
        }
    } catch (Throwable $e) {
        $warn("Category {$idnumber} was not configured: " . $e->getMessage());
    }
}
echo $warnings
    ? "Course category setup finished with {$warnings} warning(s)\n"
    : "Three bilingual course categories configured\n";
