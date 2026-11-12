import type { RefObject } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { generateModuleSchema, type GenerateModuleFormValues } from "../lib/formSchemas";

export function GenerateDialog({ dialogRef, onGenerate }: {
  dialogRef: RefObject<HTMLDialogElement | null>;
  onGenerate: (language: "vi" | "en", instructions: string) => void;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<GenerateModuleFormValues>({
    resolver: zodResolver(generateModuleSchema),
    defaultValues: { language: "vi", instructions: "" },
  });

  function onValid(values: GenerateModuleFormValues) {
    dialogRef.current?.close();
    onGenerate(values.language, values.instructions);
  }

  return (
    <dialog ref={dialogRef} aria-label="Sinh mô-đun" className="ui-dialog">
      <form noValidate onSubmit={(event) => void handleSubmit(onValid)(event)}>
        <h2 className="ui-dialog-title">Sinh mô-đun</h2>
        <label className="flex items-center gap-3 text-sm">
          Ngôn ngữ đầu ra
          <select {...register("language")} className="ui-select">
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </label>
        <label className="mt-4 block text-sm">
          Hướng dẫn thêm cho AI (tuỳ chọn)
          <textarea {...register("instructions")} maxLength={1000} rows={4} placeholder="Giải thích kỹ DLDFS và IDS" className="ui-input mt-2 w-full" />
        </label>
        {errors.instructions && <p className="mt-1 text-[13px] danger-text">{errors.instructions.message}</p>}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={() => dialogRef.current?.close()} className="ui-button">Huỷ</button>
          <button type="submit" className="ui-button primary">Sinh mô-đun</button>
        </div>
      </form>
    </dialog>
  );
}
