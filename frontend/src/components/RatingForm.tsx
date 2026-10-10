import { useEffect, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ratingSchema, emptyRating, rubric, type Rating } from "../lib/ratingSchema";
import { apiErrorMessage } from "../api/client";

const UNSAVED_PROMPT = "Đánh giá chưa lưu sẽ bị mất. Bạn vẫn muốn rời khỏi gói này?";

export function RatingForm({
  code,
  rating,
  onSave,
}: {
  code: string;
  rating: Rating | null;
  onSave: (values: Rating) => Promise<void>;
}) {
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<Rating>({
    resolver: zodResolver(ratingSchema),
    defaultValues: rating ?? emptyRating(code),
  });
  const { fields, append, remove } = useFieldArray({ control, name: "hallucination_findings" });
  // Unsaved input is not kept as a draft, so leaving it (another item, another page, logging
  // out, a reload or closing the tab) asks first. Links and the logout button are caught here
  // because the form does not own them.
  useEffect(() => {
    if (!isDirty) return;
    const onUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    const onClick = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      if (!event.target.closest("a[href], [data-leaves-page]")) return;
      if (window.confirm(UNSAVED_PROMPT)) return;
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [isDirty]);
  return (
    <form
      noValidate
      onChange={() => setSaved(false)}
      onSubmit={handleSubmit(async (values) => {
        setError("");
        setSaved(false);
        try {
          await onSave(values);
          reset(values);
          setSaved(true);
        } catch (e) {
          setError(apiErrorMessage(e, "Không lưu được đánh giá."));
        }
      })}
      className="mt-8 space-y-6"
    >
      <h2 className="text-xl font-semibold">Rubric đánh giá</h2>
      <p className="text-sm text-gray-600">
        Chấm độc lập, không đoán nguồn nội dung. Điểm 1: không đạt; 2: phần lớn chưa đạt; 3: đạt một
        phần; 4: đạt với lỗi nhỏ; 5: đạt hoàn toàn. Mỗi điểm cần nhận xét.
      </p>
      {rubric.map((criterion, i) => (
        <fieldset key={criterion.key} className="rounded border border-gray-200 bg-white p-4">
          <legend className="px-1 font-semibold">
            {i + 1}. {criterion.label}
          </legend>
          <p className="mb-3 text-sm text-gray-600">{criterion.guide}</p>
          <label className="block">
            Điểm {criterion.label}
            <select
              {...register(`${criterion.key}.score`, { valueAsNumber: true })}
              className="ml-3 rounded border p-2"
            >
              <option value="">Chọn điểm</option>
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {errors[criterion.key]?.score && (
            <p role="alert" className="text-red-700">
              Vui lòng chọn điểm nguyên từ 1 đến 5.
            </p>
          )}
          <label className="mt-3 block">
            Nhận xét {criterion.label}
            <textarea
              {...register(`${criterion.key}.justification`)}
              className="mt-1 block w-full rounded border p-2"
              rows={2}
            />
          </label>
          {errors[criterion.key]?.justification && (
            <p role="alert" className="text-red-700">
              {errors[criterion.key]?.justification?.message}
            </p>
          )}
        </fieldset>
      ))}
      <section className="space-y-3">
        <h3 className="font-semibold">Danh sách claim không có căn cứ</h3>
        <p className="text-sm">
          Không tìm thấy claim nào: để danh sách rỗng và chấm Hallucination = 5.
        </p>
        {fields.map((field, i) => (
          <fieldset key={field.id} className="space-y-2 rounded border p-3">
            <legend>Phát hiện {i + 1}</legend>
            <label className="block">
              Claim {i + 1}
              <textarea
                {...register(`hallucination_findings.${i}.claim`)}
                className="block w-full rounded border p-2"
              />
            </label>
            <label className="block">
              Vị trí {i + 1}
              <select
                {...register(`hallucination_findings.${i}.field`)}
                className="ml-2 rounded border p-2"
              >
                <option value="content">Nội dung</option>
                <option value="quiz">Quiz</option>
                <option value="explanation">Lời giải thích</option>
              </select>
            </label>
            <label className="block">
              Lý do {i + 1}
              <textarea
                {...register(`hallucination_findings.${i}.reason`)}
                className="block w-full rounded border p-2"
              />
            </label>
            {errors.hallucination_findings?.[i] && (
              <p role="alert" className="text-red-700">
                Vui lòng nhập claim và lý do.
              </p>
            )}
            <button type="button" onClick={() => remove(i)} className="text-red-700">
              Xóa phát hiện {i + 1}
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          onClick={() => append({ claim: "", field: "content", reason: "" })}
          className="rounded border px-3 py-2"
        >
          Thêm claim không có căn cứ
        </button>
      </section>
      <label className="block font-semibold">
        Ghi chú tổng quan
        <textarea
          {...register("overall_notes")}
          className="mt-2 block w-full rounded border p-3 font-normal"
          rows={3}
        />
      </label>
      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      <button
        disabled={isSubmitting}
        className="rounded bg-navy px-5 py-3 text-white disabled:opacity-50"
      >
        Lưu đánh giá
      </button>
      {saved && <p role="status">Đã lưu đánh giá.</p>}
    </form>
  );
}
