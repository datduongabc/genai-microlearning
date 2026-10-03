import { Link } from "react-router-dom";
import type { ModuleSummary } from "../api";

interface ModuleListItemProps {
  module: ModuleSummary;
  deleting: boolean;
  onDelete: (module: ModuleSummary) => void;
}

export function ModuleListItem({ module: m, deleting, onDelete }: ModuleListItemProps) {
  return (
    <li className="flex items-center gap-3">
      <Link to={`/modules/${m.id}`} className="text-sm hover:text-terracotta">
        → {m.title}
        {m.is_control && (
          <span className="ml-2 text-xs font-medium text-amber-600">Đối chứng (không RAG) - chỉ dùng cho nghiên cứu</span>
        )}
      </Link>
      <button
        onClick={() => onDelete(m)}
        disabled={deleting}
        aria-label={`Xóa mô-đun ${m.title}`}
        className="text-xs text-red-700 hover:underline disabled:opacity-60"
      >
        {deleting ? "Đang xóa..." : "Xóa mô-đun"}
      </button>
    </li>
  );
}
