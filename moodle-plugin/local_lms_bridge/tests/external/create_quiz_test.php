<?php
namespace local_lms_bridge\external;

use mod_quiz\question\display_options;
use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(create_quiz::class)]
final class create_quiz_test extends \advanced_testcase {
    public function test_creation_persists_behaviour_and_during_attempt_feedback(): void {
        global $DB;
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
                ? create_quiz::execute($course->id, $name, $questions)
                : create_quiz::execute($course->id, $name, $questions, 0, $behaviour);
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

    public function test_external_parameter_default_is_deferred_feedback(): void {
        $params = create_quiz::validate_parameters(create_quiz::execute_parameters(), [
            'courseid' => 1,
            'name' => 'Quiz',
            'questions' => [],
        ]);
        $this->assertSame('deferredfeedback', $params['behaviour']);
    }

    public function test_unknown_behaviour_is_rejected_before_creating_an_activity(): void {
        global $DB;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course();
        $quizcount = $DB->count_records('quiz');
        $modulecount = $DB->count_records('course_modules');

        try {
            create_quiz::execute($course->id, 'Invalid', [], 0, 'interactive');
            $this->fail('Unsupported behaviour was accepted');
        } catch (\invalid_parameter_exception $exception) {
            $this->assertEquals($quizcount, $DB->count_records('quiz'));
            $this->assertEquals($modulecount, $DB->count_records('course_modules'));
        }
    }
}
