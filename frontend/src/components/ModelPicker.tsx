import { useEffect, useRef, useState } from 'react'
import { apiErrorMessage, listModels, setDefaultModel } from '../api'
import type { ModelChoice } from '../api'

// Giá trị "" nghĩa là chưa chọn model nào: backend dùng mặc định của giảng viên, rồi tới
// cấu hình hệ thống. Không dùng null vì <select> chỉ giữ được chuỗi.
const THEO_CAU_HINH = ''

interface Props {
  value: string
  onChange: (alias: string) => void
}

export function ModelPicker({ value, onChange }: Props) {
  const [models, setModels] = useState<ModelChoice[]>([])
  // Mặc định đã lưu phía backend, để biết khi nào lựa chọn hiện tại khác nó và đáng lưu.
  const [saved, setSaved] = useState(THEO_CAU_HINH)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // Giữ onChange mới nhất trong ref để effect tải danh sách chỉ chạy một lần: cha có thể
  // truyền một hàm mới mỗi lần render, và chạy lại effect thì danh sách bị tải lại rồi
  // ghi đè lựa chọn giảng viên vừa đổi bằng mặc định đã lưu.
  const onChangeRef = useRef(onChange)
  useEffect(() => {
    onChangeRef.current = onChange
  })

  useEffect(() => {
    let cancelled = false
    listModels()
      .then((list) => {
        if (cancelled) return
        // Phản hồi rỗng được coi như lỗi tải chứ không phải danh sách trống: danh sách
        // trống thật thì backend vẫn trả { models: [] }.
        if (!list || !Array.isArray(list.models)) {
          setLoadFailed(true)
          return
        }
        setModels(list.models)
        const macDinh = list.default ?? THEO_CAU_HINH
        setSaved(macDinh)
        onChangeRef.current(macDinh)
      })
      .catch(() => {
        // Không tải được thì vẫn sinh được bằng cấu hình hệ thống, vì đường đó không cần
        // gateway lúc bấm nút. Nên chỉ báo, không chặn gì cả.
        if (!cancelled) setLoadFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSaveDefault() {
    setSaving(true)
    setSaveError(null)
    try {
      const list = await setDefaultModel(value || null)
      setSaved(list.default ?? THEO_CAU_HINH)
    } catch (err) {
      setSaveError(apiErrorMessage(err, 'Không lưu được mặc định'))
    } finally {
      setSaving(false)
    }
  }

  const dangChon = models.find((m) => m.alias === value)

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-3">
        <label className="model-picker">
          Model sinh
          <select
            value={loadFailed ? THEO_CAU_HINH : value}
            onChange={(e) => onChange(e.target.value)}
            disabled={loadFailed}
            className="ui-select disabled:opacity-60"
          >
            <option value={THEO_CAU_HINH}>Theo cấu hình hệ thống</option>
            {models.map((m) => (
              <option key={m.alias} value={m.alias}>
                {m.label} ({m.provider})
              </option>
            ))}
          </select>
        </label>
        {!loadFailed && value !== saved && (
          <button
            type="button"
            onClick={handleSaveDefault}
            disabled={saving}
            className="link-action"
          >
            {saving ? 'Đang lưu...' : 'Đặt làm mặc định'}
          </button>
        )}
      </div>
      {dangChon?.note && <p className="course-hint">{dangChon.note}</p>}
      {loadFailed && (
        <p className="course-hint">
          Không tải được danh sách model, sẽ sinh bằng model theo cấu hình hệ thống.
        </p>
      )}
      {saveError && <p className="text-sm danger-text">{saveError}</p>}
    </div>
  )
}
