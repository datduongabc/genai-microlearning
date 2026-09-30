<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * Tao mot Quiz roi them cac cau hoi nhieu lua chon.
 *
 * Giong mod_book, mod_quiz khong co ham web service dang ghi, nen phai dung
 * add_moduleinfo() cong voi API ngan hang cau hoi.
 */
class create_quiz extends external_api {

    /** Hanh vi lam bai duoc phep: Quiz cham cuoi bai, Practice phan hoi ngay. */
    const BEHAVIOURS = ['deferredfeedback', 'immediatefeedback'];

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid'  => new external_value(PARAM_INT, 'Ma khoa hoc trong Moodle'),
            'name'      => new external_value(PARAM_TEXT, 'Ten cua quiz'),
            'questions' => new external_multiple_structure(
                new external_single_structure([
                    'stem'    => new external_value(PARAM_RAW, 'De bai'),
                    'choices' => new external_multiple_structure(
                        new external_value(PARAM_RAW, 'Mot phuong an')
                    ),
                    'correct_index' => new external_value(PARAM_INT, 'Vi tri dap an dung, tinh tu 0'),
                    'explanation'   => new external_value(PARAM_RAW, 'Giai thich', VALUE_DEFAULT, ''),
                ])
            ),
            'sectionnum' => new external_value(PARAM_INT, 'Muc trong khoa hoc', VALUE_DEFAULT, 0),
            'behaviour'  => new external_value(
                PARAM_ALPHA,
                'Hanh vi cau hoi (preferredbehaviour): deferredfeedback hoac immediatefeedback',
                VALUE_DEFAULT,
                'deferredfeedback'
            ),
        ]);
    }

    public static function execute_returns(): external_value {
        return new external_value(PARAM_INT, 'Ma course module cua quiz vua tao');
    }

    public static function execute(int $courseid, string $name, array $questions, int $sectionnum = 0,
            string $behaviour = 'deferredfeedback'): int {
        global $CFG, $DB, $USER;
        require_once($CFG->dirroot . '/course/modlib.php');
        require_once($CFG->dirroot . '/question/engine/bank.php');
        require_once($CFG->dirroot . '/mod/quiz/locallib.php');

        $params = self::validate_parameters(self::execute_parameters(), [
            'courseid'   => $courseid,
            'name'       => $name,
            'questions'  => $questions,
            'sectionnum' => $sectionnum,
            'behaviour'  => $behaviour,
        ]);

        if (!in_array($params['behaviour'], self::BEHAVIOURS, true)) {
            throw new \invalid_parameter_exception(
                'behaviour phai la mot trong: ' . implode(', ', self::BEHAVIOURS)
            );
        }

        $course = get_course($params['courseid']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        require_capability('mod/quiz:addinstance', $context);

        $module = $DB->get_record('modules', ['name' => 'quiz'], '*', MUST_EXIST);

        $data = new \stdClass();
        $data->course = $course->id;
        $data->module = $module->id;
        $data->modulename = 'quiz';
        $data->section = $params['sectionnum'];
        $data->visible = 1;
        // Cac truong bieu mau luon co khi tao qua giao dien; add_moduleinfo() va
        // mod_quiz doc thang chung nen thieu se sinh canh bao "Undefined property".
        $data->cmidnumber = '';
        $data->timeopen = 0;
        $data->timeclose = 0;
        $data->decimalpoints = 2;
        $data->questiondecimalpoints = -1;
        $data->name = $params['name'];
        $data->intro = '';
        $data->introformat = FORMAT_HTML;
        $data->grade = 10;
        $data->questionsperpage = 1;
        $data->preferredbehaviour = $params['behaviour'];
        $data->correctnessduring = $params['behaviour'] === 'immediatefeedback';
        $data->generalfeedbackduring = $params['behaviour'] === 'immediatefeedback';
        // Cot password cua mdl_quiz khong cho phep null, va mod_quiz lay gia tri
        // tu truong bieu mau ten quizpassword roi moi gan sang cot password
        // (mod/quiz/lib.php). Dat thang $data->password se bi ghi de bang null.
        $data->quizpassword = '';

        $moduleinfo = add_moduleinfo($data, $course);
        $quiz = $DB->get_record('quiz', ['id' => $moduleinfo->instance], '*', MUST_EXIST);

        // Moodle 5.x chuyen ngan hang cau hoi thanh mot mo-dun rieng (mod_qbank):
        // cau hoi khong con nam trong context cua khoa hoc ma nam trong context
        // cua mo-dun ngan hang. Lay mo-dun mac dinh cua khoa hoc, tao neu chua co.
        $bank = \core_question\local\bank\question_bank_helper::get_default_open_instance_system_type($course, true);
        $bankcontext = \context_module::instance($bank->id);
        $category = question_get_default_category($bankcontext->id, true);
        $qtypeobj = \question_bank::get_qtype('multichoice');

        foreach ($params['questions'] as $index => $question) {
            $form = new \stdClass();
            // Moodle doi dang "idcategory,idcontext" chu khong phai mot so.
            $form->category = $category->id . ',' . $bankcontext->id;
            $form->name = $params['name'] . ' Q' . ($index + 1);
            $form->questiontext = ['text' => $question['stem'], 'format' => FORMAT_HTML];
            $form->generalfeedback = ['text' => $question['explanation'], 'format' => FORMAT_HTML];
            $form->defaultmark = 1;
            $form->penalty = 0;
            // single = 1: dung mot dap an dung, khop voi cach he thong sinh cau hoi.
            $form->single = 1;
            $form->shuffleanswers = 1;
            $form->answernumbering = 'abc';
            $form->showstandardinstruction = 0;
            $form->qtype = 'multichoice';
            // Ba truong phan hoi nay bat buoc phai co: cac cot *format tuong ung
            // trong mdl_qtype_multichoice_options khong cho phep null. De rong vi
            // loi giai thich da nam o generalfeedback.
            foreach (['correctfeedback', 'partiallycorrectfeedback', 'incorrectfeedback'] as $truong) {
                $form->{$truong} = ['text' => '', 'format' => FORMAT_HTML];
            }

            foreach ($question['choices'] as $i => $choice) {
                $form->answer[$i] = ['text' => $choice, 'format' => FORMAT_HTML];
                $form->fraction[$i] = ($i === $question['correct_index']) ? 1.0 : 0.0;
                $form->feedback[$i] = ['text' => '', 'format' => FORMAT_HTML];
            }

            $created = new \stdClass();
            $created->category = $category->id;
            $created->qtype = 'multichoice';
            $created->createdby = $USER->id;
            $created = $qtypeobj->save_question($created, $form);

            quiz_add_quiz_question($created->id, $quiz);
        }

        \mod_quiz\quiz_settings::create($quiz->id)->get_grade_calculator()->recompute_quiz_sumgrades();

        return (int) $moduleinfo->coursemodule;
    }
}
