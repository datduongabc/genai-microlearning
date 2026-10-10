<?php
// Read-only integration check: run inside a private Moodle after container startup
// and again after restarting it. The reported IDs must remain identical.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/filterlib.php');

$expected = [
    'dai-cuong' => ['vi' => 'Đại cương', 'en' => 'General Education'],
    'co-so-nganh' => ['vi' => 'Cơ sở ngành', 'en' => 'Foundation Courses'],
    'chuyen-nganh' => ['vi' => 'Chuyên ngành', 'en' => 'Specialized Courses'],
];
$categories = $DB->get_records('course_categories', [], 'idnumber', 'id,idnumber,name,parent,visible');
if (count($categories) !== 3) {
    throw new RuntimeException('Expected exactly three course categories; found ' . count($categories));
}
foreach (['en', 'vi'] as $lang) {
    if (!get_string_manager()->translation_exists($lang)) {
        throw new RuntimeException("Missing language pack: {$lang}");
    }
    force_current_language($lang);
    foreach ($categories as $category) {
        if (!isset($expected[$category->idnumber]) || $category->parent != 0 || !$category->visible) {
            throw new RuntimeException('Unexpected, nested or hidden category: ' . $category->id);
        }
        $name = format_string($category->name, true, [
            'context' => context_coursecat::instance($category->id),
        ]);
        if ($name !== $expected[$category->idnumber][$lang]) {
            throw new RuntimeException("Category {$category->id} in {$lang}: {$name}");
        }
    }
}
if (!in_array('multilang', filter_get_string_filters(), true)) {
    throw new RuntimeException('Multilang is not enabled for headings');
}
echo json_encode(array_column($categories, 'id', 'idnumber'), JSON_UNESCAPED_UNICODE) . "\n";
