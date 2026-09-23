import { z } from "zod";

// Mirrors the server (app/services/password_policy.py, app/api/auth.py) so a mistake is
// pointed out before the request is made. The server stays the judge: it also refuses
// passwords that are too common or too close to the email or name.
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

export const registerSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Vui lòng nhập họ và tên")
      .max(MAX_NAME_LENGTH, `Họ và tên tối đa ${MAX_NAME_LENGTH} ký tự`),
    email: z.email("Email không hợp lệ"),
    // Bytes, not characters: the hash only reads the first 72, and an accented Vietnamese
    // letter takes 2 to 3 of them.
    password: z
      .string()
      .min(MIN_PASSWORD_LENGTH, `Mật khẩu tối thiểu ${MIN_PASSWORD_LENGTH} ký tự`)
      .refine((value) => utf8Length(value) <= MAX_PASSWORD_BYTES, {
        message: `Mật khẩu tối đa ${MAX_PASSWORD_BYTES} byte (chữ có dấu chiếm 2 đến 3 byte)`,
      }),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Mật khẩu xác nhận không khớp",
    path: ["confirmPassword"],
  });

export type RegisterFormValues = z.infer<typeof registerSchema>;
