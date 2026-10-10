<?php
// Run after upgrades on every startup; category IDs and existing courses are retained.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/filterlib.php');

\core\session\manager::set_user(get_admin());

if (!get_string_manager()->translation_exists('vi')) {
    $controller = new \tool_langimport\controller();
    $controller->install_languagepacks(['vi']);
    get_string_manager()->reset_caches();
    if ($controller->errors || !get_string_manager()->translation_exists('vi')) {
        throw new RuntimeException('Vietnamese language pack installation failed');
    }
}

filter_set_global_state('multilang', TEXTFILTER_ON);
filter_set_applies_to_strings('multilang', true);

$names = [
    'dai-cuong' => ['Đại cương', 'General Education'],
    'co-so-nganh' => ['Cơ sở ngành', 'Foundation Courses'],
    'chuyen-nganh' => ['Chuyên ngành', 'Specialized Courses'],
];
$keptids = [];
foreach ($names as $idnumber => [$vi, $en]) {
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
        $category = \core_course_category::get($record->id, MUST_EXIST, true);
        $category->update($data);
    } else {
        $category = \core_course_category::create($data);
    }
    $keptids[] = $category->id;
}

// Remove only empty categories, deepest first. Never silently delete existing courses.
$extra = array_filter($DB->get_records('course_categories', [], 'depth DESC'),
    fn($record) => !in_array($record->id, $keptids));
foreach ($extra as $record) {
    if ($DB->record_exists('course', ['category' => $record->id])
            || $DB->record_exists('course_categories', ['parent' => $record->id])) {
        throw new RuntimeException("Category {$record->id} is no longer empty");
    }
    if ($DB->record_exists('course', ['category' => $record->id])) {
        throw new RuntimeException("Category {$record->id} contains courses; move them before removing it");
    }
}
foreach ($extra as $record) {
    $category = \core_course_category::get($record->id, MUST_EXIST, true);
    if (!$category->can_delete_full()) {
        throw new RuntimeException("Cannot remove empty category {$record->id}");
    }
    $category->delete_full(false);
}
echo "Three bilingual course categories configured\n";
