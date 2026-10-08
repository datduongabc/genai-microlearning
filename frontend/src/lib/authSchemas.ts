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

// Shared account fields for registration and admin creation.
export const createUserSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Vui lòng nhập họ và tên")
    .max(MAX_NAME_LENGTH, `Họ và tên tối đa ${MAX_NAME_LENGTH} ký tự`),
  email: z.email("Email không hợp lệ"),
  password: passwordSchema,
});

export type CreateUserFormValues = z.infer<typeof createUserSchema>;

// The new password an admin sets for a lecturer.
export const resetPasswordSchema = z.object({ password: passwordSchema });

export const registerSchema = createUserSchema.extend({ confirmPassword: z.string() }).refine(
  (data) => data.password === data.confirmPassword,
  { message: "Mật khẩu xác nhận không khớp", path: ["confirmPassword"] },
);
export type RegisterFormValues = z.infer<typeof registerSchema>;
