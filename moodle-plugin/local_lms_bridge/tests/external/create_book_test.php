<?php
namespace local_lms_bridge\external;

use PHPUnit\Framework\Attributes\CoversClass;

#[CoversClass(create_book::class)]
final class create_book_test extends \advanced_testcase {
    public function test_a_course_that_tracks_completion_counts_the_lesson_once_it_is_opened(): void {
        global $DB, $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course(['enablecompletion' => 1]);
        $chapters = [['title' => 'Bai 1', 'content' => '<p>Noi dung</p>', 'subchapter' => 0]];

        $cmid = create_book::execute($course->id, 'Bai hoc', $chapters, $USER->id);

        $cm = $DB->get_record('course_modules', ['id' => $cmid], '*', MUST_EXIST);
        $this->assertEquals(COMPLETION_TRACKING_AUTOMATIC, $cm->completion);
        $this->assertEquals(COMPLETION_VIEW_REQUIRED, $cm->completionview);
        $this->assertEquals(1, $DB->count_records('book_chapters', ['bookid' => $cm->instance]));
    }

    public function test_a_chapter_the_database_refuses_leaves_no_book_behind(): void {
        global $DB, $USER;
        $this->resetAfterTest();
        $this->setAdminUser();
        $course = $this->getDataGenerator()->create_course();
        $bookcount = $DB->count_records('book');
        $modulecount = $DB->count_records('course_modules');
        // A title longer than the 1333 characters of book_chapters.title fails after the book exists.
        $chapters = [
            ['title' => 'Bai 1', 'content' => '<p>Mot</p>', 'subchapter' => 0],
            ['title' => str_repeat('x', 1400), 'content' => '<p>Hai</p>', 'subchapter' => 0],
        ];

        try {
            create_book::execute($course->id, 'Bai hoc', $chapters, $USER->id);
            $this->fail('A chapter title over 1333 characters was accepted');
        } catch (\dml_exception $exception) {
            $this->assertEquals($bookcount, $DB->count_records('book'));
            $this->assertEquals($modulecount, $DB->count_records('course_modules'));
        }
    }
}
