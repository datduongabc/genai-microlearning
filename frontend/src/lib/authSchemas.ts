import { z } from "zod";

// Mirrors the server (app/auth/password_policy.py, app/api/admin.py) so a mistake is
// pointed out before the request is made. The server stays the judge.
const MIN_PASSWORD_LENGTH = 8;
const MAX_PASSWORD_BYTES = 72;
const MAX_NAME_LENGTH = 100;
const MAX_LOGIN_PASSWORD_LENGTH = 1024;

const utf8Length = (text: string) => new TextEncoder().encode(text).length;

export const loginSchema = z.object({
  email: z.email("Email không hợp lệ"),
  // No password rules here: an account made under older rules must still sign in.
  password: z
    .string()
    .min(1, "Vui lòng nhập mật khẩu")
    .max(MAX_LOGIN_PASSWORD_LENGTH, "Mật khẩu quá dài"),
});

export type LoginFormValues = z.infer<typeof loginSchema>;

// Bytes, not characters: the hash only reads the first 72, and an accented Vietnamese
// letter takes 2 to 3 of them.
const passwordSchema = z
  .string()
  .min(MIN_PASSWORD_LENGTH, `Mật khẩu tối thiểu ${MIN_PASSWORD_LENGTH} ký tự`)
  .refine((value) => utf8Length(value) <= MAX_PASSWORD_BYTES, {
    message: `Mật khẩu tối đa ${MAX_PASSWORD_BYTES} byte (chữ có dấu chiếm 2 đến 3 byte)`,
  });

// An edu address: x.edu, or x.edu.vn and the like. Mirrors is_edu_email in app/auth/accounts.py.
export function isEduEmail(email: string): boolean {
  const labels = email.trim().toLowerCase().split("@").pop()?.split(".") ?? [];
  if (labels.length < 2 || labels.some((label) => label === "")) return false;
  const last = labels[labels.length - 1];
  return last === "edu" || (labels.length >= 3 && labels[labels.length - 2] === "edu" && last.length === 2);
}

export const EDU_ONLY_MESSAGE = "Chỉ chấp nhận email của trường (tên miền dạng x.edu hoặc x.edu.vn)";

// The fields of a new lecturer account: the same whether an admin creates it or the lecturer signs
// themselves up, as on the server (create_account in app/auth/accounts.py).
const newAccountFields = {
  name: z
    .string()
    .trim()
    .min(1, "Vui lòng nhập họ và tên")
    .max(MAX_NAME_LENGTH, `Họ và tên tối đa ${MAX_NAME_LENGTH} ký tự`),
  email: z.email("Email không hợp lệ").refine(isEduEmail, {
    message: EDU_ONLY_MESSAGE,
    // Only for an address that is well formed: a typo gets one message, not two.
    when: (payload) => payload.issues.length === 0,
  }),
  password: passwordSchema,
};

// The form with which an admin creates a lecturer's account.
export const createLecturerSchema = z.object(newAccountFields);

export type CreateLecturerFormValues = z.infer<typeof createLecturerSchema>;

// The form with which a lecturer signs themselves up: the same fields, and the password twice.
export const registerSchema = z
  .object({ ...newAccountFields, confirmPassword: z.string() })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Mật khẩu xác nhận không khớp",
    path: ["confirmPassword"],
  });

export type RegisterFormValues = z.infer<typeof registerSchema>;

// The Moodle username and password with which a lecturer links their Moodle account. Only that the
// fields are filled in: Moodle alone says whether they are right.
export const linkMoodleSchema = z.object({
  username: z.string().trim().min(1, "Vui lòng nhập tên đăng nhập Moodle").max(100, "Tên đăng nhập quá dài"),
  password: z.string().min(1, "Vui lòng nhập mật khẩu Moodle").max(MAX_LOGIN_PASSWORD_LENGTH, "Mật khẩu quá dài"),
});

export type LinkMoodleFormValues = z.infer<typeof linkMoodleSchema>;

// The new password an admin sets for a lecturer.
export const resetPasswordSchema = z.object({ password: passwordSchema });
