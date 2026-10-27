import { z } from "zod";

// The checks of the forms outside sign-in and account admin (those live in authSchemas.ts).
// Each limit mirrors the one the server already enforces, so a mistake is pointed out before
// the request is made. The server stays the judge.

const MAX_COURSE_NAME_LENGTH = 200;
const MAX_CUSTOM_INSTRUCTIONS_LENGTH = 1000;
const MAX_CHAT_MESSAGE_LENGTH = 4000;
const MAX_MODULE_TITLE_LENGTH = 200;
const MAX_MOODLE_SECTION_NUM = 1000;

const MB = 1024 * 1024;
const AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"];
const VIDEO_EXTENSIONS = [".mp4", ".mkv", ".webm", ".mov", ".avi"];

// The form that makes a new course.
export const createCourseSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Vui lòng nhập tên môn học")
    .max(MAX_COURSE_NAME_LENGTH, `Tên môn học tối đa ${MAX_COURSE_NAME_LENGTH} ký tự`),
});

export type CreateCourseFormValues = z.infer<typeof createCourseSchema>;

// The dialog that starts generating a module from a document.
export const generateModuleSchema = z.object({
  language: z.enum(["vi", "en"]),
  instructions: z
    .string()
    .trim()
    .max(MAX_CUSTOM_INSTRUCTIONS_LENGTH, `Hướng dẫn tối đa ${MAX_CUSTOM_INSTRUCTIONS_LENGTH} ký tự`),
});

export type GenerateModuleFormValues = z.infer<typeof generateModuleSchema>;

// The size limit depends on the kind of file, as on the server (app/api/documents.py).
export function uploadSizeLimit(filename: string): { bytes: number; message: string } {
  const dot = filename.lastIndexOf(".");
  const suffix = dot === -1 ? "" : filename.slice(dot).toLowerCase();
  if (AUDIO_EXTENSIONS.includes(suffix)) return { bytes: 200 * MB, message: "Tệp âm thanh tối đa 200 MB" };
  if (VIDEO_EXTENSIONS.includes(suffix)) return { bytes: 1024 * MB, message: "Tệp video tối đa 1 GB" };
  return { bytes: 25 * MB, message: "Tài liệu tối đa 25 MB" };
}

// The file picked for upload. The type is left to the server, which explains an unsupported or
// outdated format better than a client-side list could.
export const uploadDocumentSchema = z.object({
  file: z.file("Vui lòng chọn một tệp").check((ctx) => {
    const limit = uploadSizeLimit(ctx.value.name);
    if (ctx.value.size > limit.bytes) {
      ctx.issues.push({ code: "custom", message: limit.message, input: ctx.value });
    }
  }),
});

export type UploadDocumentFormValues = z.infer<typeof uploadDocumentSchema>;

// Linking a course to one of the teacher's Moodle courses picked from the drop-down.
export const linkMoodleCourseSchema = z.object({
  moodleCourseId: z.string().min(1, "Vui lòng chọn một course Moodle"),
});

export type LinkMoodleCourseFormValues = z.infer<typeof linkMoodleCourseSchema>;

// Where in the Moodle course to push a module: an existing section, or a new one at the end.
export const publishToMoodleSchema = z.object({
  sectionNum: z.number().int().min(0).max(MAX_MOODLE_SECTION_NUM),
  addNewSection: z.boolean(),
});

export type PublishToMoodleFormValues = z.infer<typeof publishToMoodleSchema>;

// The request typed to the AI that edits a module.
export const chatEditSchema = z.object({
  message: z
    .string()
    .trim()
    .min(1, "Vui lòng nhập yêu cầu chỉnh sửa")
    .max(MAX_CHAT_MESSAGE_LENGTH, `Yêu cầu tối đa ${MAX_CHAT_MESSAGE_LENGTH} ký tự`),
});

export type ChatEditFormValues = z.infer<typeof chatEditSchema>;

export const moduleTitleSchema = z.object({
  title: z.string().max(MAX_MODULE_TITLE_LENGTH, `Tên mô-đun tối đa ${MAX_MODULE_TITLE_LENGTH} ký tự`),
});

export type ModuleTitleFormValues = z.infer<typeof moduleTitleSchema>;

export const markdownContentSchema = z.object({ markdown: z.string() });

export type MarkdownContentFormValues = z.infer<typeof markdownContentSchema>;

// A slide is edited with its bullets as one text, a line to a bullet.
export const slidesContentSchema = z.object({
  slides: z.array(z.object({ title: z.string(), bulletsText: z.string() })),
});

export type SlidesContentFormValues = z.infer<typeof slidesContentSchema>;

export const examplesContentSchema = z.object({
  examples: z.array(z.object({ context: z.string(), analysis: z.string() })),
});

export type ExamplesContentFormValues = z.infer<typeof examplesContentSchema>;

export const explanationsContentSchema = z.object({
  items: z.array(z.object({ reasoning: z.string(), explanation_text: z.string() })),
});

export type ExplanationsContentFormValues = z.infer<typeof explanationsContentSchema>;

// The edit of one quiz question. The stem and every choice must be filled in, which the form
// used to ask of the browser with `required`; the server wants at least two choices.
export const questionEditSchema = z.object({
  stem: z.string().min(1, "Vui lòng nhập câu hỏi"),
  choices: z.array(z.string().min(1, "Vui lòng nhập phương án")).min(2),
  // A string while it is edited (a radio group holds strings), a number on the wire.
  answer_key: z.string().regex(/^\d+$/).transform(Number),
  explanation: z.string(),
});

export type QuestionEditInput = z.input<typeof questionEditSchema>;
export type QuestionEditFormValues = z.output<typeof questionEditSchema>;
