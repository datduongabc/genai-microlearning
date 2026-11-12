import { useState, type RefObject } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { DEFAULT_IMAGE_MODEL, DEFAULT_VOICE, IMAGE_MODEL_CHOICES, VOICE_CHOICES, type MediaChoice } from "../api/videos";
import { generateModuleSchema, type GenerateModuleFormValues } from "../lib/formSchemas";

export type GenerateKind = "module" | "flashcards" | "video";

// What differs between the things a document can be made into. The text field is the same
// 1000-character box everywhere: guidance for a module, the focus for flashcards and video.
// A video also picks its voice and image model, which are paid per video and per picture.
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

export interface VideoMediaChoice {
  voiceAlias: string;
  imageAlias: string;
}

function MediaSelect({ label, value, onChange, choices }: {
  label: string;
  value: string;
  onChange: (alias: string) => void;
  choices: MediaChoice[];
}) {
  return (
    <label className="mt-4 flex flex-col gap-2 text-sm">
      {label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="ui-select w-full">
        {choices.map((choice) => (
          <option key={choice.alias} value={choice.alias}>
            {choice.label} ({choice.price})
          </option>
        ))}
      </select>
    </label>
  );
}

export function GenerateDialog({ dialogRef, onGenerate, kind = "module" }: {
  dialogRef: RefObject<HTMLDialogElement | null>;
  // The second argument is the module guidance or, for the other kinds, the focus. The third
  // is passed for a video only.
  onGenerate: (language: "vi" | "en", instructions: string, media?: VideoMediaChoice) => void;
  kind?: GenerateKind;
}) {
  const text = KIND_TEXT[kind];
  const [voiceAlias, setVoiceAlias] = useState(DEFAULT_VOICE);
  const [imageAlias, setImageAlias] = useState(DEFAULT_IMAGE_MODEL);
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
    if (kind === "video") onGenerate(values.language, values.instructions, { voiceAlias, imageAlias });
    else onGenerate(values.language, values.instructions);
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
        {kind === "video" && (
          <>
            <MediaSelect label="Giọng đọc" value={voiceAlias} onChange={setVoiceAlias} choices={VOICE_CHOICES} />
            <MediaSelect label="Model vẽ hình" value={imageAlias} onChange={setImageAlias} choices={IMAGE_MODEL_CHOICES} />
            <p className="course-hint">Giá tham khảo; tiền hình chỉ tính khi bấm Vẽ hình, tiền giọng khi bấm Dựng video.</p>
          </>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={() => dialogRef.current?.close()} className="ui-button">Huỷ</button>
          <button type="submit" className="ui-button primary">{text.title}</button>
        </div>
      </form>
    </dialog>
  );
}
