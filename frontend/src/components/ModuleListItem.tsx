import { Link } from "react-router-dom";
import type { ModuleSummary } from "../api";

interface ModuleListItemProps {
  module: ModuleSummary;
  deleting: boolean;
  onDelete: (module: ModuleSummary) => void;
}

export function ModuleListItem({ module: m, deleting, onDelete }: ModuleListItemProps) {
  return (
    <li className="module-item">
      <Link to={`/modules/${m.id}`} className="module-link">
        {m.title}
        {m.is_control && (
          <span className="control-badge">Đối chứng (không RAG) - chỉ dùng cho nghiên cứu</span>
        )}
      </Link>
      <button
        onClick={() => onDelete(m)}
        disabled={deleting}
        aria-label={`Xóa mô-đun ${m.title}`}
        className="link-action danger small-action"
      >
        {deleting ? "Đang xóa..." : "Xóa mô-đun"}
      </button>
    </li>
  );
}
