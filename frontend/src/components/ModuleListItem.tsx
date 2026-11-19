import { Link } from "react-router-dom";
import type { ModuleSummary } from "../api";

interface ModuleListItemProps {
  module: ModuleSummary;
  deleting: boolean;
  onDelete: (module: ModuleSummary) => void;
}

export function ModuleListItem({ module: m, deleting, onDelete }: ModuleListItemProps) {
  return (
    <li className="module-item media-item">
      <span className="media-kind">Mô-đun</span>
      <Link to={`/modules/${m.id}`} className="module-link">
        {m.title}
        {m.is_control && (
          <span className="control-badge">Bản đối chứng cho nghiên cứu, không dùng tài liệu</span>
        )}
      </Link>
      <button
        onClick={() => onDelete(m)}
        disabled={deleting}
        aria-label={`Xóa mô-đun ${m.title}`}
        className="link-action danger small-action media-delete"
      >
        {deleting ? "Đang xóa…" : "Xóa"}
      </button>
    </li>
  );
}
