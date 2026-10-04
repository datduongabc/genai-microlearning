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
    <dialog ref={dialogRef} aria-label="Sinh mô-đun" className="m-auto w-full max-w-lg rounded-[3px] p-6 backdrop:bg-black/40">
      <form noValidate onSubmit={(event) => void handleSubmit(onValid)(event)}>
        <h2 className="mb-4 text-lg font-semibold">Sinh mô-đun</h2>
        <label className="flex items-center gap-3 text-sm">
          Ngôn ngữ đầu ra
          <select {...register("language")} className="rounded-[3px] border border-gray-300 px-3 py-2">
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </label>
        <label className="mt-4 block text-sm">
          Hướng dẫn thêm cho AI (tuỳ chọn)
          <textarea {...register("instructions")} maxLength={1000} rows={4} placeholder="Giải thích kỹ DLDFS và IDS" className="mt-2 w-full rounded-[3px] border border-gray-300 p-3" />
        </label>
        {errors.instructions && <p className="mt-1 text-xs text-red-600">{errors.instructions.message}</p>}
        <div className="mt-4 flex justify-end gap-3">
          <button type="button" onClick={() => dialogRef.current?.close()} className="px-4 py-2">Huỷ</button>
          <button type="submit" className="rounded-[3px] bg-terracotta px-4 py-2 text-white">Sinh</button>
        </div>
      </form>
    </dialog>
  );
}
