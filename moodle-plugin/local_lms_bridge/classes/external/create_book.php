<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * Tao mot Book gom nhieu chuong ngan.
 *
 * mod_book chi co ham web service dang doc, khong tao duoc hoat dong moi, nen
 * ham nay goi add_moduleinfo() roi chen thang vao bang book_chapters.
 */
class create_book extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid' => new external_value(PARAM_INT, 'Ma khoa hoc trong Moodle'),
            'name'     => new external_value(PARAM_TEXT, 'Ten cua Book'),
            'chapters' => new external_multiple_structure(
                new external_single_structure([
                    'title'   => new external_value(PARAM_TEXT, 'Tieu de chuong'),
                    'content' => new external_value(PARAM_RAW, 'Noi dung chuong, dang HTML'),
                    // So nguyen chu khong phai bool: Moodle doc truong bieu mau nay
                    // duoi dang so va se tu choi chuoi "True".
                    'subchapter' => new external_value(PARAM_INT, '1 neu la chuong con, nguoc lai 0'),
                ])
            ),
            'actorid' => new external_value(PARAM_INT, 'Linked Moodle user whose capability authorizes this write'),
            'sectionnum' => new external_value(PARAM_INT, 'Muc trong khoa hoc', VALUE_DEFAULT, 0),
        ]);
    }

    public static function execute_returns(): external_value {
        return new external_value(PARAM_INT, 'Ma course module cua Book vua tao');
    }

    public static function execute(int $courseid, string $name, array $chapters, int $actorid, int $sectionnum = 0): int {
        global $CFG, $DB;
        require_once($CFG->dirroot . '/course/modlib.php');

        $params = self::validate_parameters(self::execute_parameters(), [
            'actorid' => $actorid,
            'courseid'   => $courseid,
            'name'       => $name,
            'chapters'   => $chapters,
            'sectionnum' => $sectionnum,
        ]);

        $course = get_course($params['courseid']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        \local_lms_bridge\course_access::require_author($params['actorid'], $context);
        require_capability('moodle/course:manageactivities', $context);

        $module = $DB->get_record('modules', ['name' => 'book'], '*', MUST_EXIST);

        $data = new \stdClass();
        $data->course = $course->id;
        $data->module = $module->id;
        $data->modulename = 'book';
        $data->section = $params['sectionnum'];
        $data->visible = 1;
        $data->name = $params['name'];
        $data->intro = '';
        $data->introformat = FORMAT_HTML;
        $data->numbering = 1;
        $data->navstyle = 1;
        $data->customtitles = 0;
        // Done when opened, so course completion can count the lesson (see create_quiz).
        $data->completion = COMPLETION_TRACKING_AUTOMATIC;
        $data->completionview = COMPLETION_VIEW_REQUIRED;
        $data->completionexpected = 0;

        $moduleinfo = add_moduleinfo($data, $course);

        $pagenum = 1;
        foreach ($params['chapters'] as $chapter) {
            $row = new \stdClass();
            $row->bookid = $moduleinfo->instance;
            $row->pagenum = $pagenum++;
            $row->subchapter = $chapter['subchapter'];
            $row->title = $chapter['title'];
            $row->content = $chapter['content'];
            $row->contentformat = FORMAT_HTML;
            $row->hidden = 0;
            $row->timecreated = time();
            $row->timemodified = time();
            $row->importsrc = '';
            $DB->insert_record('book_chapters', $row);
        }

        return (int) $moduleinfo->coursemodule;
    }
}
