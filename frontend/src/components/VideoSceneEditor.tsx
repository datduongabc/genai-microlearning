import { useId } from 'react'
import { MAX_BULLETS, type SceneKind, type VideoScene } from '../api'
import type { Limiter } from '../lib/limiter'
import {
  flagMessage,
  flagsFor,
  NO_SOURCE,
  needsReview,
  sceneFlags,
  slideVersion,
  uncited as isUncited,
  WHOLE_ITEM_LABEL,
  type ItemDraft,
  type SceneDraft,
} from '../lib/videoDraft'
import { AutoGrowTextarea } from './AutoGrowTextarea'
import { ScenePreview, SlidePlaceholder } from './ScenePreview'
import { SourcePassages } from './SourcePassages'

const KIND_LABEL: Record<SceneKind, string> = {
  title: 'Mở đầu',
  illustration: 'Minh họa',
  formula: 'Công thức',
  code: 'Code',
  table: 'Bảng',
  summary: 'Tóm tắt',
}

function FlagNote({ id, flag, dismissLabel, onDismiss }: { id?: string; flag: string; dismissLabel: string; onDismiss: () => void }) {
  return (
    <div className="doc-warning video-flag">
      <span id={id}>{flagMessage(flag)}</span>
      <button type="button" onClick={onDismiss} aria-label={dismissLabel} className="link-action small-action">
        Bỏ đánh dấu
      </button>
    </div>
  )
}

function ItemEditor({ item, name, flags, uncited, onText, onDismiss, onRemove, maxLength }: {
  item: ItemDraft
  uncited: boolean
  // "ý 2 cảnh 1": the accessible names of the item's controls.
  name: string
  flags: string[]
  onText: (text: string) => void
  onDismiss: (flag: string) => void
  onRemove: () => void
  maxLength: number
}) {
  const id = useId()
  const label = name.charAt(0).toUpperCase() + name.slice(1)
  // What is wrong with the item is read with its field, not only seen next to it.
  const notes = describedBy([...(uncited ? [`${id}-no-source`] : []), ...flags.map((_, i) => `${id}-flag-${i}`)])
  return (
    <li className="video-item">
      <div className="video-item-row">
        <AutoGrowTextarea
          aria-label={label}
          aria-describedby={notes}
          value={item.text}
          // One sentence is one line on the server, so a line break would split it in two.
          onChange={(e) => onText(e.target.value.replace(/\n/g, ' '))}
          maxLength={maxLength}
          rows={2}
          className="ui-input w-full"
        />
        <button type="button" onClick={onRemove} aria-label={`Xóa ${name}`} className="link-action quiet-danger small-action">
          Xóa
        </button>
      </div>
      {uncited && <p id={`${id}-no-source`} className="doc-warning">{NO_SOURCE}</p>}
      {flags.map((flag, i) => (
        <FlagNote key={`${i}-${flag}`} id={`${id}-flag-${i}`} flag={flag} dismissLabel={`Bỏ đánh dấu ${name}`}
          onDismiss={() => onDismiss(flag)} />
      ))}
    </li>
  )
}

const describedBy = (ids: string[]) => (ids.length > 0 ? ids.join(' ') : undefined)

interface SceneSlide {
  videoId: string
  limiter: Limiter
  picturesDrawn: number
  // The saved scene this one came from, which is what its slide shows.
  origin: VideoScene | undefined
  // Whether the scene differs from that saved one.
  changed: boolean
}

// One scene: read as the learner will see it (its slide and what the voice says) until the lecturer
// opens it with "Sửa", as one part of a module is opened; then every field, with "Lưu" and "Hủy".
export function VideoSceneEditor({ scene: s, number, count, slide, canDraw, nextKey, onChange, onMove, onRemove, onDraw,
  open, locked, saving, onOpen, onSave, onCancel }: {
  scene: SceneDraft
  number: number
  count: number
  slide: SceneSlide
  canDraw: boolean
  nextKey: () => number
  onChange: (change: (scene: SceneDraft) => SceneDraft) => void
  onMove: (by: number) => void
  onRemove: () => void
  onDraw: (index: number) => void
  open: boolean
  // Another scene is open, or something is being saved: nothing here can start.
  locked: boolean
  saving: boolean
  onOpen: () => void
  onSave: () => void
  onCancel: () => void
}) {
  const { origin } = slide
  const id = useId()
  // Flags about the whole scene describe its title, and those about a field describe that field.
  const ofScene = sceneFlags(s.flags)
  const sceneNotes = ofScene.map((_, i) => `${id}-scene-${i}`)
  const wholeLabel = WHOLE_ITEM_LABEL[s.kind]
  const ofField = wholeLabel ? flagsFor(s.flags, wholeLabel) : []
  const fieldNotes = describedBy(ofField.map((_, i) => `${id}-field-${i}`))
  const drawable = origin !== undefined && s.fromIndex !== null && origin.kind === 'illustration' && !!origin.image_prompt?.trim()
  const set = (change: Partial<SceneDraft>) => onChange((scene) => ({ ...scene, ...change }))
  const dismiss = (flag: string) => onChange((scene) => ({ ...scene, flags: scene.flags.filter((f) => f !== flag) }))
  const editItems = (field: 'bullets' | 'sentences', change: (items: ItemDraft[]) => ItemDraft[]) =>
    onChange((scene) => ({ ...scene, [field]: change(scene[field]) }))
  const itemProps = (field: 'bullets' | 'sentences', item: ItemDraft) => ({
    item,
    flags: flagsFor(s.flags, item.label),
    uncited: isUncited(s, item),
    onText: (text: string) => editItems(field, (items) => items.map((x) => (x.key === item.key ? { ...x, text } : x))),
    onDismiss: dismiss,
    onRemove: () => editItems(field, (items) => items.filter((x) => x.key !== item.key)),
  })
  const addItem = (field: 'bullets' | 'sentences') =>
    editItems(field, (items) => [...items, { key: nextKey(), text: '', citations: [], label: null }])

  const preview = (
    <>
      {origin && s.fromIndex !== null ? (
        <ScenePreview
          key={s.fromIndex}
          videoId={slide.videoId}
          index={s.fromIndex}
          version={slideVersion(origin, s.fromIndex, slide.picturesDrawn)}
          number={number}
          limiter={slide.limiter}
          note={slide.changed ? 'Đây là slide của bản đã lưu. Lưu để xem bản đang sửa.' : undefined}
        />
      ) : (
        <SlidePlaceholder text="Lưu để xem slide của cảnh mới." />
      )}
      {drawable && (
        <div className="video-picture-row">
          <span className="course-hint">
            {origin.image_outdated ? 'Hình được vẽ theo mô tả cũ.' : origin.has_image ? 'Đã có hình minh họa.' : 'Chưa vẽ hình minh họa.'}
          </span>
          <button
            type="button"
            onClick={() => onDraw(s.fromIndex as number)}
            disabled={!canDraw}
            aria-label={`${origin.has_image ? 'Vẽ lại' : 'Vẽ'} hình cảnh ${number}`}
            className="link-action small-action"
          >
            {origin.has_image ? 'Vẽ lại hình' : 'Vẽ hình'}
          </button>
        </div>
      )}
    </>
  )
  const head = (
    <div className="flashcard-head">
      <div className="video-scene-title">
        <span className="part-label">Cảnh {number}</span>
        <span className="video-scene-kind">{KIND_LABEL[s.kind]}</span>
        {needsReview(s) && <span className="video-flag-badge">Cần xem lại</span>}
      </div>
      {!open && (
        <div className="video-scene-actions">
          <button type="button" onClick={onOpen} disabled={locked} aria-label={`Sửa cảnh ${number}`}
            className="link-action small-action">Sửa</button>
          <button type="button" onClick={() => onMove(-1)} disabled={locked || number === 1}
            aria-label={`Đưa cảnh ${number} lên`} className="link-action small-action">Lên</button>
          <button type="button" onClick={() => onMove(1)} disabled={locked || number === count}
            aria-label={`Đưa cảnh ${number} xuống`} className="link-action small-action">Xuống</button>
          <button type="button" onClick={onRemove} disabled={locked || count === 1} aria-label={`Xóa cảnh ${number}`}
            className="link-action danger small-action">
            Xóa
          </button>
        </div>
      )}
    </div>
  )

  if (!open) {
    // What to look at again, each with the bullet or sentence it is about ("Câu 2: ..."), without the controls that change it.
    const notes = [...s.flags, ...[...s.bullets, ...s.sentences].filter((item) => isUncited(s, item))
      .map((item) => `${item.label}: ${NO_SOURCE.toLowerCase()}`)]
    return (
      <li aria-label={`Cảnh ${number}`} className="flashcard-item video-scene">
        {head}
        <div className="video-scene-body">
          <div className="video-scene-slide">{preview}</div>
          <div className="video-scene-text">
        {notes.length > 0 && (
          <ul className="check-reasons" aria-label={`Lý do cảnh ${number} cần xem lại`}>
            {notes.map((note, i) => <li key={`${i}-${note}`}>{note}</li>)}
          </ul>
        )}
        {s.sentences.some((item) => item.text.trim() !== '') && (
          <div className="video-scene-voice">
            <p className="video-scene-label">Lời thoại</p>
            <p>{s.sentences.map((item) => item.text.trim()).filter(Boolean).join(' ')}</p>
          </div>
        )}
          </div>
        </div>
      </li>
    )
  }

  return (
    <li aria-label={`Cảnh ${number}`} className="flashcard-item video-scene" data-open="true">
      {head}
      {/* In the place it has when closed, so opening the scene does not fetch its slide again. */}
      <div className="video-scene-body">
        <div className="video-scene-slide">{preview}</div>
      </div>
      {ofScene.map((flag, i) => (
        <FlagNote key={`${i}-${flag}`} id={sceneNotes[i]} flag={flag} dismissLabel={`Bỏ đánh dấu cảnh ${number}`}
          onDismiss={() => dismiss(flag)} />
      ))}

      <div className="video-scene-fields">
        <label className="flashcard-field">
          Loại cảnh
          <select aria-label={`Loại cảnh ${number}`} value={s.kind}
            onChange={(e) => set({ kind: e.target.value as SceneKind })} className="ui-select">
            {(Object.keys(KIND_LABEL) as SceneKind[]).map((kind) => (
              <option key={kind} value={kind}>{KIND_LABEL[kind]}</option>
            ))}
          </select>
        </label>
        <label className="flashcard-field">
          Tiêu đề
          <input aria-label={`Tiêu đề cảnh ${number}`} aria-describedby={describedBy(sceneNotes)} value={s.title} maxLength={300}
            onChange={(e) => set({ title: e.target.value })} className="ui-input w-full" />
        </label>
      </div>

      <div className="flashcard-field">
        Ý chính trên slide
        <ul className="video-items" aria-label={`Ý chính cảnh ${number}`}>
          {s.bullets.map((item, i) => (
            <ItemEditor key={item.key} name={`ý ${i + 1} cảnh ${number}`} maxLength={500} {...itemProps('bullets', item)} />
          ))}
        </ul>
        <button type="button" disabled={s.bullets.length >= MAX_BULLETS} onClick={() => addItem('bullets')}
          className="link-action small-action self-start">
          Thêm ý
        </button>
      </div>

      {s.kind === 'formula' && (
        <label className="flashcard-field">
          Công thức (LaTeX)
          <AutoGrowTextarea aria-label={`Công thức cảnh ${number}`} aria-describedby={fieldNotes} value={s.formula} maxLength={2000} rows={2}
            onChange={(e) => set({ formula: e.target.value })} className="ui-input w-full font-mono" />
        </label>
      )}
      {s.kind === 'code' && (
        <label className="flashcard-field">
          Code
          <AutoGrowTextarea aria-label={`Mã nguồn cảnh ${number}`} aria-describedby={fieldNotes} value={s.code} maxLength={4000} rows={6} spellCheck={false}
            onChange={(e) => set({ code: e.target.value })} className="ui-input w-full font-mono" />
        </label>
      )}
      {s.kind === 'table' && (
        <label className="flashcard-field">
          Bảng
          <AutoGrowTextarea aria-label={`Bảng cảnh ${number}`} aria-describedby={fieldNotes} value={s.table} rows={4}
            onChange={(e) => set({ table: e.target.value })} className="ui-input w-full font-mono" />
          <span className="course-hint">Mỗi dòng là một hàng. Các ô cách nhau bằng dấu |.</span>
        </label>
      )}
      {wholeLabel && ofField.map((flag, i) => (
        <FlagNote key={`${i}-${flag}`} id={`${id}-field-${i}`} flag={flag} dismissLabel={`Bỏ đánh dấu ${wholeLabel.toLowerCase()} cảnh ${number}`}
          onDismiss={() => dismiss(flag)} />
      ))}
      {s.kind === 'illustration' && (
        <label className="flashcard-field">
          Mô tả hình minh họa
          <AutoGrowTextarea aria-label={`Mô tả hình cảnh ${number}`} value={s.imagePrompt} maxLength={2000} rows={3}
            onChange={(e) => set({ imagePrompt: e.target.value })} className="ui-input w-full" />
          <span className="course-hint">
            Ghi rõ số liệu và nhãn cần có trong hình. Để trống mô tả thì slide chỉ có chữ.
          </span>
        </label>
      )}

      <div className="flashcard-field">
        Lời thoại (mỗi ô một câu)
        <ul className="video-items" aria-label={`Lời thoại cảnh ${number}`}>
          {s.sentences.map((item, i) => (
            <ItemEditor key={item.key} name={`câu ${i + 1} cảnh ${number}`} maxLength={6000} {...itemProps('sentences', item)} />
          ))}
        </ul>
        <button type="button" onClick={() => addItem('sentences')} className="link-action small-action self-start">
          Thêm câu
        </button>
      </div>

      <details className="video-scene-sources">
        <summary>Đoạn nguồn của cảnh ({s.sources.length})</summary>
        <SourcePassages sources={s.sources} label={`Nguồn của cảnh ${number}`} />
      </details>
      {s.title.trim() === '' && <p className="text-sm danger-text">Cảnh cần một tiêu đề.</p>}
      <div className="flashcard-actions">
        <button type="button" onClick={onSave} disabled={locked || s.title.trim() === ''} className="ui-button primary">
          {saving ? 'Đang lưu…' : 'Lưu'}
        </button>
        <button type="button" onClick={onCancel} disabled={locked} className="ui-button">Hủy</button>
      </div>
    </li>
  )
}
