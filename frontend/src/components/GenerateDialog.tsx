import { useState, type RefObject } from "react";

export function GenerateDialog({ dialogRef, onGenerate }: {
  dialogRef: RefObject<HTMLDialogElement | null>;
  onGenerate: (language: "vi" | "en", instructions: string) => void;
}) {
  const [language, setLanguage] = useState<"vi" | "en">("vi");
  const [instructions, setInstructions] = useState("");
  return (
    <dialog ref={dialogRef} aria-label="Sinh mô-đun" className="m-auto w-full max-w-lg rounded-[3px] p-6 backdrop:bg-black/40">
      <form onSubmit={(event) => {
        event.preventDefault();
        dialogRef.current?.close();
        onGenerate(language, instructions.trim());
      }}>
        <h2 className="mb-4 text-lg font-semibold">Sinh mô-đun</h2>
        <label className="flex items-center gap-3 text-sm">
          Ngôn ngữ đầu ra
          <select value={language} onChange={(e) => setLanguage(e.target.value as "vi" | "en")} className="rounded-[3px] border border-gray-300 px-3 py-2">
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </label>
        <label className="mt-4 block text-sm">
          Hướng dẫn thêm cho AI (tuỳ chọn)
          <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={1000} rows={4} placeholder="Giải thích kỹ DLDFS và IDS" className="mt-2 w-full rounded-[3px] border border-gray-300 p-3" />
        </label>
        <div className="mt-4 flex justify-end gap-3">
          <button type="button" onClick={() => dialogRef.current?.close()} className="px-4 py-2">Huỷ</button>
          <button type="submit" className="rounded-[3px] bg-terracotta px-4 py-2 text-white">Sinh</button>
        </div>
      </form>
    </dialog>
  );
}
