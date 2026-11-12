import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage } from "../api";
import { homeFor, useAuth } from "../auth/useAuth";
import { loginSchema, type LoginFormValues } from "../lib/authSchemas";
import { AuthLayout } from "../components/AuthLayout";

const SUPPORT_EMAIL = "dat.duongtandat385@gmail.com";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const justRegistered = (useLocation().state as { justRegistered?: boolean } | null)?.justRegistered === true;

  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({ resolver: zodResolver(loginSchema) });

  async function onSubmit(values: LoginFormValues) {
    setError(null);
    try {
      const user = await login(values.email, values.password);
      navigate(homeFor(user.is_admin));
    } catch (err) {
      setError(apiErrorMessage(err, "Đăng nhập thất bại"));
    }
  }

  return (
    <AuthLayout>
      <form
        onSubmit={handleSubmit(onSubmit)}
        noValidate
        className="auth-form"
      >
        <h1>Đăng nhập</h1>

        {justRegistered && !error && (
          <div role="status" className="text-sm text-green-800">
            ✓ Đã tạo tài khoản. Mời đăng nhập.
          </div>
        )}

        {error && (
          <div role="alert" className="ui-alert">
            {error}
          </div>
        )}

        <label className="auth-field">
          <span>
            Email
          </span>
          <input
            type="email"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="ten@truong.edu.vn"
            className="auth-input"
            aria-invalid={!!errors.email}
            aria-describedby={errors.email ? "email-error" : undefined}
            {...register("email")}
          />
          {errors.email && (
            <span id="email-error" className="text-sm text-red-700">{errors.email.message}</span>
          )}
        </label>

        <div className="auth-field">
          <div className="flex items-center justify-between text-sm">
            <label htmlFor="login-password" className="font-medium">Mật khẩu</label>
            <span className="forgot">
              <a href={`mailto:${SUPPORT_EMAIL}`} aria-describedby="forgot-tip" className="text-accent underline underline-offset-2">Quên mật khẩu?</a>
              <span id="forgot-tip" role="tooltip" className="tip">Liên hệ quản trị viên để đặt lại mật khẩu.</span>
            </span>
          </div>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            placeholder="Tối thiểu 8 ký tự"
            className="auth-input"
            aria-invalid={!!errors.password}
            aria-describedby={errors.password ? "password-error" : undefined}
            {...register("password")}
          />
          {errors.password && (
            <span id="password-error" className="text-sm text-red-700">{errors.password.message}</span>
          )}
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className="auth-submit"
        >
          {isSubmitting ? "Đang đăng nhập..." : "Đăng nhập"}
        </button>

        <p className="auth-footer">Chưa có tài khoản? <Link to="/register" className="text-accent hover:underline">Đăng ký</Link></p>
      </form>
    </AuthLayout>
  );
}
