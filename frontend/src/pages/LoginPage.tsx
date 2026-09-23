import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage } from "../api";
import { useAuth } from "../auth/useAuth";
import { loginSchema, type LoginFormValues } from "../lib/authSchemas";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const justRegistered = (location.state as { justRegistered?: boolean } | null)
    ?.justRegistered;

  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({ resolver: zodResolver(loginSchema) });

  async function onSubmit(values: LoginFormValues) {
    setError(null);
    try {
      await login(values.email, values.password);
      navigate("/courses");
    } catch (err) {
      setError(apiErrorMessage(err, "Đăng nhập thất bại"));
    }
  }

  return (
    <div className="relative flex min-h-screen bg-cream">
      <div className="absolute top-8 right-10 z-10 flex items-center gap-2 text-xs tracking-wide text-gray-500">
        <span className="font-semibold text-[#1c1c1c] underline underline-offset-4">
          Tiếng Việt
        </span>
        <span className="text-gray-300">|</span>
        <span>English</span>
      </div>

      <div className="hidden w-95 flex-col justify-between bg-navy p-10 text-[#f3f1ea] md:flex">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white p-1">
            <img
              src="/logo.png"
              className="h-full w-full object-contain"
              alt=""
            />
          </div>
          <div>
            <div className="font-serif text-lg font-semibold">HELLO LMS</div>
            <div className="text-[10px] uppercase tracking-widest text-[#b9c2cc]">
              Central Authentication Service
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <div className="h-px w-full bg-[#3a4a5c]" />
          <div className="text-[11px] text-[#8d97a2]">
            Copyright © 2011 - 2012 Ho Chi Minh University of Technology
          </div>
        </div>
      </div>

      <div className="flex flex-1 items-center justify-center p-10">
        <form
          onSubmit={handleSubmit(onSubmit)}
          noValidate
          className="flex w-full max-w-sm flex-col gap-7"
        >
          <div className="flex flex-col gap-1">
            <div className="text-xs font-semibold uppercase tracking-wider text-terracotta">
              Đăng nhập
            </div>
            <h1 className="font-serif text-2xl font-semibold">
              Nhập Email Và Mật Khẩu
            </h1>
          </div>

          {justRegistered && (
            <div className="rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
              Tạo tài khoản thành công, vui lòng đăng nhập.
            </div>
          )}
          {error && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Email
            </span>
            <input
              type="email"
              autoComplete="username"
              placeholder="ten@hcmut.edu.vn"
              className="border-0 border-b-[1.5px] border-gray-300 bg-transparent py-2.5 text-[15px] outline-none focus:border-terracotta"
              {...register("email")}
            />
            {errors.email && (
              <span className="text-xs text-red-600">{errors.email.message}</span>
            )}
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Mật khẩu
            </span>
            <input
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              className="border-0 border-b-[1.5px] border-gray-300 bg-transparent py-2.5 text-[15px] outline-none focus:border-terracotta"
              {...register("password")}
            />
            {errors.password && (
              <span className="text-xs text-red-600">{errors.password.message}</span>
            )}
          </label>

          <button
            type="submit"
            disabled={isSubmitting}
            className="rounded-[3px] bg-terracotta py-3.5 text-sm font-semibold text-white transition hover:bg-terracotta-dark disabled:opacity-60"
          >
            {isSubmitting ? "Đang đăng nhập..." : "Đăng nhập"}
          </button>

          <div className="flex items-center gap-1.5 text-sm text-gray-500">
            <span>Chưa có tài khoản?</span>
            <Link
              to="/register"
              className="font-semibold text-terracotta hover:underline"
            >
              Đăng ký
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}
