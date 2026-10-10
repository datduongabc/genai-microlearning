import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { getRaters, listStudies, setRaters, type Study } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { raterTextSchema } from "../lib/ratingSchema";

export function AdminStudiesPanel() {
  const [studies, setStudies] = useState<Study[]>([]);
  const [studyId, setStudyId] = useState("");
  const [loadedId, setLoadedId] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const {
    register,
    reset,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.input<typeof raterTextSchema>, unknown, z.output<typeof raterTextSchema>>({
    resolver: zodResolver(raterTextSchema),
    defaultValues: { emailsText: "" },
  });
  useEffect(() => {
    let active = true;
    listStudies()
      .then((data) => {
        if (active) setStudies(data);
      })
      .catch(() => {
        if (active) setError("Không tải được nghiên cứu.");
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    if (!studyId) return;
    getRaters(studyId)
      .then((data) => {
        if (active) {
          reset({ emailsText: data.emails.join("\n") });
          setLoadedId(studyId);
        }
      })
      .catch(() => {
        if (active) setError("Không tải được người chấm.");
      });
    return () => {
      active = false;
    };
  }, [studyId, reset]);
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold">Người chấm nghiên cứu</h2>
      <p className="mb-4 text-sm text-gray-600">
        Mời tài khoản đang hoạt động bằng email. Danh sách mới thay thế toàn bộ danh sách hiện tại;
        để trống để thu hồi mọi lời mời.
      </p>
      <label className="block">
        Nghiên cứu
        <select
          aria-label="Nghiên cứu"
          value={studyId}
          disabled={isSubmitting}
          onChange={(e) => {
            setStudyId(e.target.value);
            setError("");
            setMessage("");
            setLoadedId("");
          }}
          className="mt-2 mb-4 block w-full rounded border p-2"
        >
          <option value="">Chọn nghiên cứu</option>
          {studies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p role="alert" className="mb-3 text-red-700">
          {error}
        </p>
      )}
      {studyId && loadedId === studyId && (
        <form
          noValidate
          onSubmit={handleSubmit(async (values) => {
            setError("");
            setMessage("");
            try {
              const data = await setRaters(studyId, values.emails);
              reset({ emailsText: data.emails.join("\n") });
              setMessage("Đã lưu người chấm.");
            } catch (e) {
              setError(apiErrorMessage(e, "Không lưu được người chấm."));
            }
          })}
        >
          <label className="block">
            Email người chấm (mỗi dòng một email)
            <textarea
              {...register("emailsText")}
              className="mt-2 block w-full rounded border p-3"
              rows={5}
            />
          </label>
          {Object.keys(errors).length > 0 && (
            <p role="alert" className="text-red-700">
              Mỗi dòng phải là một email hợp lệ, tối đa 100 người chấm.
            </p>
          )}
          <button
            disabled={isSubmitting}
            className="mt-4 rounded bg-navy px-4 py-2 text-white disabled:opacity-50"
          >
            Lưu người chấm
          </button>
          {message && (
            <p role="status" className="mt-3">
              {message}
            </p>
          )}
        </form>
      )}
      {studies.length === 0 && !error && <p>Chưa có nghiên cứu.</p>}
    </section>
  );
}
