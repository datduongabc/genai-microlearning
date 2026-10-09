import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModelPicker } from '../../../components/ModelPicker'
import * as api from '../../../api'
import type { ModelList } from '../../../api'

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

const danhSach: ModelList = {
  models: [
    { alias: 'gemini-fast', label: 'Gemini Flash Lite', provider: 'gemini', note: 'nhanh, có hạn mức miễn phí' },
    { alias: 'groq-pro', label: 'GPT-OSS 120B qua Groq', provider: 'groq', note: null },
  ],
  default: null,
}

// Bọc trong một cha có state thật, vì ô chọn là component được điều khiển: kiểm nó với
// một onChange giả không đổi value thì không thấy được lựa chọn có giữ lại hay không.
function Khung({ onChange = vi.fn() }: { onChange?: (alias: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <ModelPicker
      value={value}
      onChange={(alias) => {
        setValue(alias)
        onChange(alias)
      }}
    />
  )
}

function oChon() {
  return screen.getByRole('combobox', { name: /model sinh/i }) as HTMLSelectElement
}

beforeEach(() => {
  vi.mocked(api.listModels).mockReset()
  vi.mocked(api.setDefaultModel).mockReset()
})

describe('ModelPicker', () => {
  it('liệt kê các model kèm nhà cung cấp', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    render(<Khung />)

    expect(await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'GPT-OSS 120B qua Groq (groq)' })).toBeInTheDocument()
  })

  it('chưa có mặc định thì chọn sẵn cấu hình hệ thống', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    render(<Khung />)

    await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })
    expect(oChon().value).toBe('')
  })

  it('có mặc định đã lưu thì chọn sẵn nó và báo lên cha', async () => {
    vi.mocked(api.listModels).mockResolvedValue({ ...danhSach, default: 'groq-pro' })
    const onChange = vi.fn()
    render(<Khung onChange={onChange} />)

    await waitFor(() => expect(oChon().value).toBe('groq-pro'))
    expect(onChange).toHaveBeenCalledWith('groq-pro')
  })

  it('hiện ghi chú của model đang chọn để giảng viên biết nên chọn cái nào', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    render(<Khung />)
    await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })

    await userEvent.selectOptions(oChon(), 'gemini-fast')

    expect(screen.getByText('nhanh, có hạn mức miễn phí')).toBeInTheDocument()
  })

  it('chỉ hiện nút đặt mặc định khi lựa chọn khác với mặc định đã lưu', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    render(<Khung />)
    await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })

    expect(screen.queryByRole('button', { name: /đặt làm mặc định/i })).not.toBeInTheDocument()
    await userEvent.selectOptions(oChon(), 'groq-pro')
    expect(screen.getByRole('button', { name: /đặt làm mặc định/i })).toBeInTheDocument()
  })

  it('đặt làm mặc định thì lưu đúng alias và nút biến mất', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    vi.mocked(api.setDefaultModel).mockResolvedValue({ ...danhSach, default: 'groq-pro' })
    render(<Khung />)
    await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })

    await userEvent.selectOptions(oChon(), 'groq-pro')
    await userEvent.click(screen.getByRole('button', { name: /đặt làm mặc định/i }))

    expect(api.setDefaultModel).toHaveBeenCalledWith('groq-pro')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /đặt làm mặc định/i })).not.toBeInTheDocument(),
    )
  })

  it('chọn lại cấu hình hệ thống rồi đặt mặc định thì xoá mặc định đã lưu', async () => {
    vi.mocked(api.listModels).mockResolvedValue({ ...danhSach, default: 'groq-pro' })
    vi.mocked(api.setDefaultModel).mockResolvedValue({ ...danhSach, default: null })
    render(<Khung />)
    await waitFor(() => expect(oChon().value).toBe('groq-pro'))

    await userEvent.selectOptions(oChon(), '')
    await userEvent.click(screen.getByRole('button', { name: /đặt làm mặc định/i }))

    expect(api.setDefaultModel).toHaveBeenCalledWith(null)
  })

  it('không tải được danh sách thì báo rõ và vẫn để sinh bằng cấu hình hệ thống', async () => {
    // Gateway sập thì backend trả 503. Trang không được vỡ theo: sinh mô-đun bằng model
    // mặc định vẫn làm được, vì đường đó không cần gateway lúc bấm nút.
    vi.mocked(api.listModels).mockRejectedValue(new Error('503'))
    const onChange = vi.fn()
    render(<Khung onChange={onChange} />)

    expect(await screen.findByText(/không tải được danh sách model/i)).toBeInTheDocument()
    expect(oChon()).toBeDisabled()
    expect(oChon().value).toBe('')
  })

  it('phản hồi rỗng cũng không làm vỡ trang', async () => {
    vi.mocked(api.listModels).mockResolvedValue(undefined as unknown as ModelList)
    render(<Khung />)

    expect(await screen.findByText(/không tải được danh sách model/i)).toBeInTheDocument()
  })

  it('lưu mặc định thất bại thì báo lỗi và giữ nguyên nút để thử lại', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    vi.mocked(api.setDefaultModel).mockRejectedValue(new Error('422'))
    render(<Khung />)
    await screen.findByRole('option', { name: 'Gemini Flash Lite (gemini)' })

    await userEvent.selectOptions(oChon(), 'groq-pro')
    await userEvent.click(screen.getByRole('button', { name: /đặt làm mặc định/i }))

    expect(await screen.findByText(/không lưu được mặc định/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /đặt làm mặc định/i })).toBeInTheDocument()
  })
})
