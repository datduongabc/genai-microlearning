import { AuthLayout } from "../components/AuthLayout";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "react-router-dom";
import { apiErrorMessage, register as registerUser } from "../api";
import { registerSchema, type RegisterFormValues } from "../lib/authSchemas";

export function RegisterPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFormValues>({ resolver: zodResolver(registerSchema) });

  async function onSubmit(values: RegisterFormValues) {
    setError(null);
    try {
      await registerUser(values.name, values.email, values.password);
      navigate("/login", { state: { justRegistered: true } });
    } catch (err) {
      setError(apiErrorMessage(err, "Đăng ký thất bại"));
    }
  }

  return (
    <AuthLayout>
        <form
          onSubmit={handleSubmit(onSubmit)}
          noValidate
          className="auth-form"
        >
          <div className="flex flex-col gap-1">
            <h1 >
              Tạo tài khoản
            </h1>
          </div>

          {error && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          <label className="auth-field">
            <span>
              Họ và tên
            </span>
            <input
              type="text"
              autoComplete="name"
              placeholder="Nguyễn Văn A"
              className="auth-input"
              aria-invalid={!!errors.name}
              aria-describedby={errors.name ? "name-error" : undefined}
              {...register("name")}
            />
            {errors.name && (
              <span id="name-error" className="text-sm text-red-700">{errors.name.message}</span>
            )}
          </label>

          <label className="auth-field">
            <span>
              Email
            </span>
            <input
              type="email"
              autoComplete="username"
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

          <label className="auth-field">
            <span>
              Mật khẩu
            </span>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Tối thiểu 8 ký tự"
              className="auth-input"
              aria-invalid={!!errors.password}
              aria-describedby={errors.password ? "password-error" : undefined}
              {...register("password")}
            />
            {errors.password && (
              <span id="password-error" className="text-sm text-red-700">{errors.password.message}</span>
            )}
          </label>

          <label className="auth-field">
            <span>
              Nhập lại mật khẩu
            </span>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Nhập lại mật khẩu"
              className="auth-input"
              aria-invalid={!!errors.confirmPassword}
              aria-describedby={errors.confirmPassword ? "confirmPassword-error" : undefined}
              {...register("confirmPassword")}
            />
            {errors.confirmPassword && (
              <span id="confirmPassword-error" className="text-sm text-red-700">
                {errors.confirmPassword.message}
              </span>
            )}
          </label>

          <button
            type="submit"
            disabled={isSubmitting}
            className="auth-submit"
          >
            {isSubmitting ? "Đang tạo tài khoản..." : "Tạo tài khoản"}
          </button>

          <div className="auth-footer">
            <span>Đã có tài khoản? </span>
            <Link
              to="/login"
              className="font-semibold text-terracotta hover:underline"
            >
              Đăng nhập
            </Link>
          </div>
        </form>
    </AuthLayout>
  );
}
