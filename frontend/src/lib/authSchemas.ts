import { z } from "zod";

// Mirrors the server (app/api/auth.py) so a missing field is pointed out before the request is
// made. The server stays the judge.
const MAX_USERNAME_LENGTH = 254;
const MAX_LOGIN_PASSWORD_LENGTH = 1024;

// A lecturer signs in with their Moodle username and password, an admin with their email and the
// local password: so no rule about the shape of the username, and none about the password either
// (an account made under older rules must still sign in).
export const loginSchema = z.object({
  username: z
    .string()
    .trim()
    .min(1, "Vui lòng nhập tên đăng nhập")
    .max(MAX_USERNAME_LENGTH, "Tên đăng nhập quá dài"),
  password: z
    .string()
    .min(1, "Vui lòng nhập mật khẩu")
    .max(MAX_LOGIN_PASSWORD_LENGTH, "Mật khẩu quá dài"),
});

export type LoginFormValues = z.infer<typeof loginSchema>;
