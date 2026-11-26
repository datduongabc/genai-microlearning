import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { listModuleConcepts, type MediaChoice, type ModuleConcept } from "../api";
import { generateModuleSchema, type GenerateModuleFormValues } from "../lib/formSchemas";
import { useMediaModels } from "../lib/useMediaModels";
import { ModelPicker } from "./ModelPicker";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";

export type GenerateKind = "module" | "flashcards" | "video";

// What differs between the things a document can be made into. The text field is the same
// 1000-character box everywhere: guidance for a module, the focus for flashcards and video.
// A video also picks its voice and image model, which are paid per video and per picture.
const KIND_TEXT: Record<GenerateKind, { title: string; field: string; tooLong: string }> = {
  module: {
    title: "Tạo mô-đun",
    // What the module is about is the concept picked above; this is guidance on how to write it.
    field: "Yêu cầu thêm cho mô-đun (không bắt buộc)",
    tooLong: "",
  },
  flashcards: {
    title: "Tạo flashcard",
    field: "Flashcard nên tập trung vào nội dung gì?",
    tooLong: "Tối đa 1000 ký tự",
  },
  video: {
    title: "Tạo video",
    field: "Video nên tập trung vào nội dung gì?",
    tooLong: "Tối đa 1000 ký tự",
  },
};

// The models a generation runs on. "" for the text model means the lecturer's saved default, then
// the system's. Voice and image are for a video only, undefined when their list could not be
// loaded: the server then uses its defaults.
export interface GenerateChoice {
  modelAlias: string;
  voiceAlias?: string;
  imageAlias?: string;
  // A module's concept, picked from the planner's list; undefined leaves it to the server.
  concept?: ModuleConcept;
}

type Concepts = ModuleConcept[] | "loading" | "failed";

// The planner's concepts for a module, by document and language. Asked when the dialog opens and when
// the language changes, never as the guidance is typed, and kept, so switching back costs nothing.
function useModuleConcepts(documentId: string | undefined, language: "vi" | "en", wanted: boolean): Concepts | null {
  const [byKey, setByKey] = useState<Record<string, ModuleConcept[] | "failed">>({});
  const asked = useRef(new Set<string>());
  const key = documentId ? `${documentId}:${language}` : null;
  useEffect(() => {
    if (!wanted || !documentId || !key || asked.current.has(key)) return;
    asked.current.add(key);
    // Not cancelled when the language changes: the answer is kept under its own key for later.
    listModuleConcepts(documentId, language).then(
      (concepts) => setByKey((was) => ({ ...was, [key]: concepts.length ? concepts : "failed" })),
      () => {
        // Asked again the next time the dialog opens: a planner that timed out has often finished by then.
        asked.current.delete(key);
        setByKey((was) => ({ ...was, [key]: "failed" }));
      },
    );
  }, [wanted, documentId, language, key]);
  return wanted && key ? (byKey[key] ?? "loading") : null;
}

function MediaSelect({ label, value, onChange, choices }: {
  label: string;
  value: string;
  onChange: (alias: string) => void;
  // null while the server's list is on its way.
  choices: MediaChoice[] | null;
}) {
  return (
    <label className="mt-4 flex flex-col gap-2 text-sm">
      {label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} disabled={!choices}
        className="ui-select w-full disabled:opacity-60">
        {/* As for the text model: "" leaves the choice to the server. */}
        <option value="">{choices ? "Mặc định của hệ thống" : "Đang tải…"}</option>
        {choices?.map((choice) => (
          <option key={choice.alias} value={choice.alias}>
            {choice.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function GenerateDialog({ open, onOpenChange, returnFocusTo, onGenerate, kind = "module", documentId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // What gets focus back on close; the menu item that opened the dialog no longer exists then.
  returnFocusTo?: HTMLElement | null;
  // The second argument is the module guidance or, for the other kinds, the focus.
  onGenerate: (language: "vi" | "en", instructions: string, choice: GenerateChoice) => void;
  kind?: GenerateKind;
  // The document a module is for: its concepts are listed to pick from.
  documentId?: string;
}) {
  const text = KIND_TEXT[kind];
  const models = useMediaModels(kind === "video");
  const choices = models && models !== "failed" ? models : null;
  // "" until the lecturer picks one: the system's default, which the server applies.
  const [voicePick, setVoicePick] = useState("");
  const [imagePick, setImagePick] = useState("");
  const [modelAlias, setModelAlias] = useState("");
  const voiceAlias = voicePick || undefined;
  const imageAlias = imagePick || undefined;
  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<GenerateModuleFormValues>({
    resolver: zodResolver(generateModuleSchema),
    defaultValues: { language: "vi", instructions: "" },
  });
  const concepts = useModuleConcepts(documentId, useWatch({ control, name: "language" }), kind === "module" && open);
  // The name picked; "" until the list is there, which then means its first, the most important.
  const [conceptPick, setConceptPick] = useState("");
  const listed = Array.isArray(concepts) ? concepts : null;
  const concept = listed ? (listed.find((c) => c.name === conceptPick) ?? listed[0]) : undefined;

  function onValid(values: GenerateModuleFormValues) {
    onOpenChange(false);
    onGenerate(values.language, values.instructions,
      kind === "video" ? { modelAlias, voiceAlias, imageAlias } : concept ? { modelAlias, concept } : { modelAlias });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="ui-dialog" aria-describedby={undefined}
        onCloseAutoFocus={(event) => { if (returnFocusTo) { event.preventDefault(); returnFocusTo.focus(); } }}>
      <form noValidate onSubmit={(event) => void handleSubmit(onValid)(event)}>
        <DialogTitle className="ui-dialog-title">{text.title}</DialogTitle>
        <label className="flex flex-col gap-2 text-sm">
          Ngôn ngữ đầu ra
          <select {...register("language")} className="ui-select w-full">
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </label>
        {concepts !== null && (
          concepts === "failed" ? (
            <p className="course-hint mt-4">Chưa lấy được danh sách khái niệm. Mô-đun sẽ tự chọn khái niệm quan trọng nhất của tài liệu.</p>
          ) : (
            <label className="mt-4 flex flex-col gap-2 text-sm">
              Khái niệm của mô-đun
              <select aria-label="Khái niệm của mô-đun" value={concept?.name ?? ""} disabled={!listed}
                onChange={(e) => setConceptPick(e.target.value)} className="ui-select w-full disabled:opacity-60">
                {listed ? listed.map((c, i) => (
                  <option key={c.name} value={c.name}>{i === 0 ? `${c.name} (quan trọng nhất)` : c.name}</option>
                )) : <option value="">Đang tìm các khái niệm trong tài liệu…</option>}
              </select>
              {concept?.summary && <span className="course-hint">{concept.summary}</span>}
            </label>
          )
        )}
        <label className="mt-4 flex flex-col gap-2 text-sm">
          {text.field}
          <textarea {...register("instructions")} maxLength={1000} rows={4} className="ui-input w-full" />
        </label>
        {errors.instructions && <p className="mt-1 text-sm danger-text">{text.tooLong || errors.instructions.message}</p>}
        <ModelPicker value={modelAlias} onChange={setModelAlias} />
        {kind === "video" && (
          <>
            {models === "failed" ? (
              <p className="course-hint">Không tải được danh sách giọng đọc và mô hình vẽ hình.</p>
            ) : (
              <>
                <MediaSelect label="Giọng đọc" value={voicePick} onChange={setVoicePick} choices={choices?.voices ?? null} />
                <MediaSelect label="Vẽ hình" value={imagePick} onChange={setImagePick} choices={choices?.images ?? null} />
              </>
            )}
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
