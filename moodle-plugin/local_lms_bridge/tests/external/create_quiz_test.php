<?php
namespace local_lms_bridge\external;

use mod_quiz\question\display_options;
use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(create_quiz::class)]
final class create_quiz_test extends \advanced_testcase {
    public function test_creation_persists_behaviour_and_during_attempt_feedback(): void {
        global $DB, $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course();
        $questions = [[
            'stem' => 'Which answer is correct?',
            'choices' => ['Wrong', 'Right'],
            'correct_index' => 1,
            'explanation' => 'The second answer is correct because of the explanation.',
        ]];

        foreach ([null, 'deferredfeedback', 'immediatefeedback'] as $index => $behaviour) {
            $name = 'Boundary ' . $index;
            $cmid = $behaviour === null
                ? create_quiz::execute($course->id, $name, $questions, $USER->id)
                : create_quiz::execute($course->id, $name, $questions, $USER->id, 0, $behaviour);
            $cm = get_coursemodule_from_id('quiz', $cmid, $course->id, false, MUST_EXIST);
            $quiz = $DB->get_record('quiz', ['id' => $cm->instance], '*', MUST_EXIST);
            $this->assertSame($behaviour ?? 'deferredfeedback', $quiz->preferredbehaviour);
            $this->assertEquals(1, $DB->count_records('quiz_slots', ['quizid' => $quiz->id]));
            $this->assertEquals(1, $quiz->sumgrades);
            $question = $DB->get_record('question', ['name' => $name . ' Q1'], '*', MUST_EXIST);
            $this->assertSame($questions[0]['explanation'], $question->generalfeedback);
            $options = display_options::make_from_quiz($quiz, display_options::DURING);
            $visibility = $behaviour === 'immediatefeedback' ? display_options::VISIBLE : display_options::HIDDEN;
            $this->assertSame($visibility, $options->correctness);
            $this->assertSame($visibility, $options->generalfeedback);
        }
    }

    public function test_a_course_that_tracks_completion_counts_the_quiz_when_passed_and_the_practice_when_graded(): void {
        global $CFG, $DB, $USER;
        require_once($CFG->libdir . '/gradelib.php');
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course(['enablecompletion' => 1]);
        $questions = [['stem' => 'Q?', 'choices' => ['A', 'B'], 'correct_index' => 0, 'explanation' => '']];

        foreach (['deferredfeedback' => [1, 5.0], 'immediatefeedback' => [0, 0.0]] as $behaviour => [$pass, $gradepass]) {
            $cmid = create_quiz::execute($course->id, $behaviour, $questions, $USER->id, 0, $behaviour);
            $cm = $DB->get_record('course_modules', ['id' => $cmid], '*', MUST_EXIST);
            $this->assertEquals(COMPLETION_TRACKING_AUTOMATIC, $cm->completion);
            $this->assertEquals(0, $cm->completiongradeitemnumber);
            $this->assertEquals($pass, $cm->completionpassgrade);
            $item = \grade_item::fetch(['itemtype' => 'mod', 'itemmodule' => 'quiz', 'iteminstance' => $cm->instance,
                'courseid' => $course->id]);
            $this->assertEquals($gradepass, (float) $item->gradepass);
        }
    }

    public function test_a_course_without_completion_tracking_gets_none(): void {
        global $DB, $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course(['enablecompletion' => 0]);
        $questions = [['stem' => 'Q?', 'choices' => ['A', 'B'], 'correct_index' => 0, 'explanation' => '']];

        $cmid = create_quiz::execute($course->id, 'Quiz', $questions, $USER->id);

        $this->assertEquals(COMPLETION_TRACKING_NONE, $DB->get_field('course_modules', 'completion', ['id' => $cmid]));
    }

    public function test_external_parameter_default_is_deferred_feedback(): void {
        $params = create_quiz::validate_parameters(create_quiz::execute_parameters(), [
            'courseid' => 1,
            'name' => 'Quiz',
            'actorid' => 2,
            'questions' => [],
        ]);
        $this->assertSame('deferredfeedback', $params['behaviour']);
    }

    public function test_unknown_behaviour_is_rejected_before_creating_an_activity(): void {
        global $DB, $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course();
        $quizcount = $DB->count_records('quiz');
        $modulecount = $DB->count_records('course_modules');

        try {
            create_quiz::execute($course->id, 'Invalid', [], $USER->id, 0, 'interactive');
            $this->fail('Unsupported behaviour was accepted');
        } catch (\invalid_parameter_exception $exception) {
            $this->assertEquals($quizcount, $DB->count_records('quiz'));
            $this->assertEquals($modulecount, $DB->count_records('course_modules'));
        }
    }
}
