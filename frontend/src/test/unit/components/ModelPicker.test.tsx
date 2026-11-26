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
    { alias: 'gemini-flash', label: 'Gemini 3.8 Flash', provider: 'google', note: 'nhanh, đọc được ảnh' },
    { alias: 'deepseek-flash', label: 'DeepSeek V4.1 Flash', provider: 'deepseek', note: null },
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
  return screen.getByRole('combobox', { name: /^mô hình$/i }) as HTMLSelectElement
}

beforeEach(() => {
  vi.mocked(api.listModels).mockReset()
  vi.mocked(api.setDefaultModel).mockReset()
})

describe('ModelPicker', () => {
  it('liệt kê model với mặc định hệ thống, hiện nút đặt mặc định khi đổi và lưu alias mới', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    vi.mocked(api.setDefaultModel).mockResolvedValue({ ...danhSach, default: 'deepseek-flash' })
    render(<Khung />)

    expect(await screen.findByRole('option', { name: 'Gemini 3.8 Flash (google)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'DeepSeek V4.1 Flash (deepseek)' })).toBeInTheDocument()
    expect(oChon().value).toBe('')
    expect(screen.queryByRole('button', { name: /đặt làm mặc định/i })).not.toBeInTheDocument()

    await userEvent.selectOptions(oChon(), 'deepseek-flash')
    expect(screen.getByRole('button', { name: /đặt làm mặc định/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /đặt làm mặc định/i }))
    expect(api.setDefaultModel).toHaveBeenCalledWith('deepseek-flash')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /đặt làm mặc định/i })).not.toBeInTheDocument(),
    )
  })

  it('có mặc định đã lưu thì chọn sẵn nó và báo lên cha', async () => {
    vi.mocked(api.listModels).mockResolvedValue({ ...danhSach, default: 'deepseek-flash' })
    const onChange = vi.fn()
    render(<Khung onChange={onChange} />)

    await waitFor(() => expect(oChon().value).toBe('deepseek-flash'))
    expect(onChange).toHaveBeenCalledWith('deepseek-flash')
  })

  it('chọn lại cấu hình hệ thống rồi đặt mặc định thì xoá mặc định đã lưu', async () => {
    vi.mocked(api.listModels).mockResolvedValue({ ...danhSach, default: 'deepseek-flash' })
    vi.mocked(api.setDefaultModel).mockResolvedValue({ ...danhSach, default: null })
    render(<Khung />)
    await waitFor(() => expect(oChon().value).toBe('deepseek-flash'))

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

    expect(await screen.findByText(/không tải được danh sách mô hình/i)).toBeInTheDocument()
    expect(oChon()).toBeDisabled()
    expect(oChon().value).toBe('')
  })

  it('phản hồi rỗng cũng không làm vỡ trang', async () => {
    vi.mocked(api.listModels).mockResolvedValue(undefined as unknown as ModelList)
    render(<Khung />)

    expect(await screen.findByText(/không tải được danh sách mô hình/i)).toBeInTheDocument()
  })

  it('lưu mặc định thất bại thì báo lỗi và giữ nguyên nút để thử lại', async () => {
    vi.mocked(api.listModels).mockResolvedValue(danhSach)
    vi.mocked(api.setDefaultModel).mockRejectedValue(new Error('422'))
    render(<Khung />)
    await screen.findByRole('option', { name: 'Gemini 3.8 Flash (google)' })

    await userEvent.selectOptions(oChon(), 'deepseek-flash')
    await userEvent.click(screen.getByRole('button', { name: /đặt làm mặc định/i }))

    expect(await screen.findByText(/không lưu được mặc định/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /đặt làm mặc định/i })).toBeInTheDocument()
  })
})
