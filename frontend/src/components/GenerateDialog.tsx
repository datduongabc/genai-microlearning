import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import type { MediaChoice } from "../api";
import { generateModuleSchema, type GenerateModuleFormValues } from "../lib/formSchemas";
import { useMediaModels } from "../lib/useMediaModels";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";

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

// Undefined when the choices could not be loaded: the server then uses its defaults.
export interface VideoMediaChoice {
  voiceAlias?: string;
  imageAlias?: string;
}

function MediaSelect({ label, value, onChange, choices }: {
  label: string;
  value: string | undefined;
  onChange: (alias: string) => void;
  // null while the server's list is on its way.
  choices: MediaChoice[] | null;
}) {
  return (
    <label className="mt-4 flex flex-col gap-2 text-sm">
      {label}
      <select aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={!choices}
        className="ui-select w-full disabled:opacity-60">
        {!choices && <option value="">Đang tải...</option>}
        {choices?.map((choice) => (
          <option key={choice.alias} value={choice.alias}>
            {choice.label} ({choice.price})
          </option>
        ))}
      </select>
    </label>
  );
}

export function GenerateDialog({ open, onOpenChange, returnFocusTo, onGenerate, kind = "module" }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // What gets focus back on close; the menu item that opened the dialog no longer exists then.
  returnFocusTo?: HTMLElement | null;
  // The second argument is the module guidance or, for the other kinds, the focus. The third
  // is passed for a video only.
  onGenerate: (language: "vi" | "en", instructions: string, media?: VideoMediaChoice) => void;
  kind?: GenerateKind;
}) {
  const text = KIND_TEXT[kind];
  const models = useMediaModels(kind === "video");
  const choices = models && models !== "failed" ? models : null;
  // null until the lecturer picks one: the server's default is shown and sent.
  const [voicePick, setVoicePick] = useState<string | null>(null);
  const [imagePick, setImagePick] = useState<string | null>(null);
  const voiceAlias = voicePick ?? choices?.default_voice;
  const imageAlias = imagePick ?? choices?.default_image;
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<GenerateModuleFormValues>({
    resolver: zodResolver(generateModuleSchema),
    defaultValues: { language: "vi", instructions: "" },
  });

  function onValid(values: GenerateModuleFormValues) {
    onOpenChange(false);
    if (kind === "video") onGenerate(values.language, values.instructions, { voiceAlias, imageAlias });
    else onGenerate(values.language, values.instructions);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="ui-dialog" aria-describedby={undefined}
        onCloseAutoFocus={(event) => { if (returnFocusTo) { event.preventDefault(); returnFocusTo.focus(); } }}>
      <form noValidate onSubmit={(event) => void handleSubmit(onValid)(event)}>
        <DialogTitle className="ui-dialog-title">{text.title}</DialogTitle>
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
        {errors.instructions && <p className="mt-1 text-sm danger-text">{text.tooLong || errors.instructions.message}</p>}
        {kind === "video" && (
          <>
            {models === "failed" ? (
              <p className="course-hint">
                Không tải được danh sách giọng và model ảnh: video sẽ dùng giọng và model ảnh mặc định.
              </p>
            ) : (
              <>
                <MediaSelect label="Giọng đọc" value={voiceAlias} onChange={setVoicePick} choices={choices?.voices ?? null} />
                <MediaSelect label="Model vẽ hình" value={imageAlias} onChange={setImagePick} choices={choices?.images ?? null} />
              </>
            )}
            <p className="course-hint">Giá tham khảo; tiền hình chỉ tính khi bấm Vẽ hình, tiền giọng khi bấm Dựng video.</p>
          </>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={() => onOpenChange(false)} className="ui-button">Hủy</button>
          <button type="submit" className="ui-button primary">{text.title}</button>
        </div>
      </form>
      </DialogContent>
    </Dialog>
  );
}
