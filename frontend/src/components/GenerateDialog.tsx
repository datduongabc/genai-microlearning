import type { RefObject } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { generateModuleSchema, type GenerateModuleFormValues } from "../lib/formSchemas";

export type GenerateKind = "module" | "flashcards" | "video";

// What differs between the things a document can be made into. The text field is the same
// 1000-character box everywhere: guidance for a module, the focus for flashcards and video.
// A new kind's extra choices (the voice and image model of a video) go beside the field below.
const KIND_TEXT: Record<GenerateKind, { title: string; field: string; placeholder: string; tooLong: string }> = {
  module: {
    title: "Sinh mô-đun",
    field: "Hướng dẫn thêm cho AI (tuỳ chọn)",
    placeholder: "Giải thích kỹ DLDFS và IDS",
    tooLong: "",
  },
  flashcards: {
    title: "Sinh flashcard",
    field: "Trọng tâm (tuỳ chọn)",
    placeholder: "Mảng và danh sách liên kết",
    tooLong: "Trọng tâm tối đa 1000 ký tự",
  },
  video: {
    title: "Sinh video",
    field: "Trọng tâm (tuỳ chọn)",
    placeholder: "Chỉ phần BFS và DFS",
    tooLong: "Trọng tâm tối đa 1000 ký tự",
  },
};

export function GenerateDialog({ dialogRef, onGenerate, kind = "module" }: {
  dialogRef: RefObject<HTMLDialogElement | null>;
  // The second argument is the module guidance or, for the other kinds, the focus.
  onGenerate: (language: "vi" | "en", instructions: string) => void;
  kind?: GenerateKind;
}) {
  const text = KIND_TEXT[kind];
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
    <dialog ref={dialogRef} aria-label={text.title} className="ui-dialog">
      <form noValidate onSubmit={(event) => void handleSubmit(onValid)(event)}>
        <h2 className="ui-dialog-title">{text.title}</h2>
        <label className="flex items-center gap-3 text-sm">
          Ngôn ngữ đầu ra
          <select {...register("language")} className="ui-select">
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </label>
        <label className="mt-4 block text-sm">
          {text.field}
          <textarea {...register("instructions")} maxLength={1000} rows={4} placeholder={text.placeholder} className="ui-input mt-2 w-full" />
        </label>
        {errors.instructions && <p className="mt-1 text-[13px] danger-text">{text.tooLong || errors.instructions.message}</p>}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={() => dialogRef.current?.close()} className="ui-button">Huỷ</button>
          <button type="submit" className="ui-button primary">{text.title}</button>
        </div>
      </form>
    </dialog>
  );
}
