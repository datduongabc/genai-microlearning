import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ratingSchema, emptyRating, rubric, type Rating } from "../lib/ratingSchema";
import { apiErrorMessage } from "../api/client";
import { notifyDone, notifyFailed } from "../lib/notify";

const UNSAVED_PROMPT = "Đánh giá chưa lưu sẽ bị mất. Bạn vẫn muốn rời khỏi gói này?";
const BLOOM_LABELS: Record<string, string> = {
  remember: "Nhớ", understand: "Hiểu", apply: "Áp dụng",
  analyze: "Phân tích", evaluate: "Đánh giá", create: "Sáng tạo",
};

// One scale for every criterion; each criterion only asks its question.
const SCALE = ["Không đạt", "Phần lớn chưa đạt", "Đạt một phần", "Đạt, có lỗi nhỏ", "Đạt hoàn toàn"];

export function RatingForm({
  code,
  bloomLevel,
  rating,
  onSave,
}: {
  code: string;
  bloomLevel: string;
  rating: Rating | null;
  onSave: (values: Rating) => Promise<void>;
}) {
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set([rubric[0].key]));
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
  const values = useWatch({ control });
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
      onSubmit={handleSubmit(async (values) => {
        try {
          await onSave(values);
          reset(values);
          notifyDone("Đã lưu đánh giá.");
        } catch (e) {
          notifyFailed(apiErrorMessage(e, "Không lưu được đánh giá."));
        }
      }, (invalid) => setOpened((prev) => new Set([...prev, ...rubric.filter((c) => invalid[c.key]).map((c) => c.key)])))}
      className="rating-form ui-card"
    >
      <header className="rubric-header"><h2>Tiêu chí chấm</h2>
        <details className="scale-help"><summary>Thang điểm</summary><ol>{SCALE.map((text, i) => <li key={i}><strong>{i + 1}</strong>{text}</li>)}</ol></details>
      </header>
      {rubric.map((criterion, i) => (
        <details key={criterion.key} className="criterion" open={opened.has(criterion.key)}
          onToggle={(e) => {
            const open = e.currentTarget.open;
            setOpened((prev) => {
              if (prev.has(criterion.key) === open) return prev;
              const next = new Set(prev);
              if (open) next.add(criterion.key); else next.delete(criterion.key);
              return next;
            });
          }}>
          {/* The score given so far sits at the end of the row, so the closed list reads as progress. */}
          <summary>
            <span className="criterion-name">{i + 1}. {criterion.label}</span>
            {values[criterion.key]?.score
              ? <span className="criterion-score">{values[criterion.key]?.score}/5</span>
              : <span className="criterion-score empty">Chưa chấm</span>}
          </summary>
          <fieldset><legend className="sr-only">{criterion.label}</legend>
          {/* bloom_fidelity is judged against the declared level, so it is shown here only, not in the packet. */}
          {criterion.key === "bloom_fidelity" && <p className="small muted">Mức Bloom đặt ra: {BLOOM_LABELS[bloomLevel] ?? bloomLevel}</p>}
          <p className="rubric-question">{criterion.question}</p>
          <Controller control={control} name={`${criterion.key}.score`} render={({ field }) => (
            <div className="scores" role="radiogroup" aria-label={`Điểm ${criterion.label}`}>
              {[1, 2, 3, 4, 5].map((n) => <label key={n}>
                <input type="radio" name={field.name} value={n} checked={field.value === n}
                  ref={n === 1 ? field.ref : undefined} onBlur={field.onBlur} onChange={() => field.onChange(n)} />
                <span>{n}</span>
              </label>)}
            </div>
          )} />
          {errors[criterion.key]?.score && (
            <p role="alert" className="danger-text">
              Vui lòng chọn điểm nguyên từ 1 đến 5.
            </p>
          )}
          <label className="mt-3 block text-sm">
            Nhận xét{" "}<span className="sr-only">{criterion.label}</span>
            <textarea
              {...register(`${criterion.key}.justification`)}
              className="ui-input mt-1 w-full"
              rows={2}
            />
          </label>
          {errors[criterion.key]?.justification && (
            <p role="alert" className="danger-text">
              {errors[criterion.key]?.justification?.message}
            </p>
          )}
          </fieldset>
        </details>
      ))}
      <section className="criterion space-y-3">
        <div className="claims-header"><h3 className="font-semibold">Khẳng định không có căn cứ <span className="count">{fields.length}</span></h3>
          <button type="button" onClick={() => append({ claim: "", field: "content", reason: "" })}
            className="link-button inline-flex items-center gap-1" aria-label="Thêm khẳng định không có căn cứ"><Plus aria-hidden="true" className="size-4" />Thêm</button>
        </div>
        {fields.map((field, i) => (
          <fieldset key={field.id} className="finding">
            <div className="finding-head">
              <legend>Phát hiện {i + 1}</legend>
              <button type="button" onClick={() => remove(i)} className="finding-remove" aria-label={`Xóa phát hiện ${i + 1}`}>
                <Trash2 aria-hidden="true" className="size-4" />
                Xóa
              </button>
            </div>
            <label className="finding-field">
              <span>Khẳng định{" "}<span className="sr-only">{i + 1}</span></span>
              <textarea
                {...register(`hallucination_findings.${i}.claim`)}
                rows={2}
                className="ui-input w-full"
              />
            </label>
            <label className="finding-field">
              <span>Vị trí{" "}<span className="sr-only">{i + 1}</span></span>
              <select
                {...register(`hallucination_findings.${i}.field`)}
                className="ui-select w-full"
              >
                <option value="content">Nội dung</option>
                <option value="slides">Slide</option>
                <option value="example">Ví dụ</option>
                <option value="quiz">Quiz</option>
                <option value="practice">Bài luyện tập</option>
                <option value="explanation">Lời giải thích</option>
              </select>
            </label>
            <label className="finding-field">
              <span>Lý do{" "}<span className="sr-only">{i + 1}</span></span>
              <textarea
                {...register(`hallucination_findings.${i}.reason`)}
                rows={2}
                className="ui-input w-full"
              />
            </label>
            {errors.hallucination_findings?.[i] && (
              <p role="alert" className="danger-text">
                Nhập khẳng định và lý do.
              </p>
            )}
          </fieldset>
        ))}
      </section>
      <details className="criterion rating-notes"><summary><span className="criterion-name">Ghi chú chung</span></summary>
      <label className="mt-3 block">
        <span className="sr-only">Ghi chú tổng quan</span>
        <textarea
          {...register("overall_notes")}
          className="ui-input w-full"
          rows={3}
        />
      </label>
      </details>
      <footer className="rating-footer"><span>Còn {rubric.filter((c) => !values[c.key]?.score || !values[c.key]?.justification?.trim()).length} tiêu chí</span>
      <button
        disabled={isSubmitting}
        className="ui-button primary"
      >
        Lưu đánh giá
      </button>
      </footer>
    </form>
  );
}
