import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SectionTree } from '../../../components/SectionTree'
import * as api from '../../../api'
import type { ModuleSummary, SectionInfo } from '../../../api'

// Every function of the module is replaced except the one that words an error: that is
// real logic the pages rely on, not a call to the server.
vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

function section(id: string, title: string, parentId: string | null = null, extra: Partial<SectionInfo> = {}): SectionInfo {
  return {
    id,
    parent_id: parentId,
    title,
    level: parentId ? 2 : 1,
    page_start: 1,
    page_end: 10,
    module_count: 0,
    generating: false,
    ...extra,
  }
}

const outline = [
  section('s1', 'Chapter 1'),
  section('s2', 'Section 1.1', 's1'),
  section('s3', 'Chapter 2'),
]

type TreeProps = React.ComponentProps<typeof SectionTree>

function renderTree(props: Partial<TreeProps> = {}) {
  const onGenerated = vi.fn()
  const tree = (extra: Partial<TreeProps> = {}) => (
    <MemoryRouter>
      <SectionTree documentId="doc-1" documentGenerating={false} modules={[]} onGenerated={onGenerated} onDeleteModule={vi.fn()} deletingModuleId={null} {...props} {...extra} />
    </MemoryRouter>
  )
  const view = render(tree())
  return { onGenerated, rerenderWith: (extra: Partial<TreeProps>) => view.rerender(tree(extra)) }
}

const checkbox = (name: RegExp) => screen.getByRole('checkbox', { name })
const generateButton = () => screen.getByRole('button', { name: /sinh mô-đun/i })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listSections).mockResolvedValue(outline)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SectionTree', () => {
  it('lists every section of the outline with its pages', async () => {
    renderTree()

    expect(await screen.findByText(/Chapter 1/)).toBeInTheDocument()
    expect(screen.getByText(/Section 1\.1/)).toBeInTheDocument()
    expect(screen.getByText(/Chapter 2/)).toBeInTheDocument()
    expect(screen.getAllByText(/trang 1-10/)).toHaveLength(3)
  })

  it('keeps the generate button disabled until something is picked', async () => {
    renderTree()
    await screen.findByText(/Chapter 1/)

    expect(generateButton()).toBeDisabled()

    await userEvent.click(checkbox(/Chapter 2/))

    expect(generateButton()).toBeEnabled()
    expect(generateButton()).toHaveTextContent('Sinh mô-đun cho 1 mục')
  })

  it('locks the sections inside a picked section and the section that contains it', async () => {
    renderTree()
    await screen.findByText(/Chapter 1/)

    await userEvent.click(checkbox(/Chapter 1/))
    expect(checkbox(/Section 1\.1/)).toBeDisabled()
    expect(checkbox(/Chapter 2/)).toBeEnabled()

    await userEvent.click(checkbox(/Chapter 1/))
    await userEvent.click(checkbox(/Section 1\.1/))
    expect(checkbox(/Chapter 1/)).toBeDisabled()
  })

  it('refuses more than five sections at once and says why', async () => {
    vi.mocked(api.listSections).mockResolvedValue(
      Array.from({ length: 6 }, (_, i) => section(`t${i}`, `Topic ${i}`)),
    )
    renderTree()
    await screen.findByText(/Topic 0/)

    for (let i = 0; i < 6; i++) await userEvent.click(checkbox(new RegExp(`Topic ${i}`)))

    expect(generateButton()).toBeDisabled()
    expect(screen.getByText(/tối đa 5 mục/i)).toBeInTheDocument()
  })

  it('sends the model the teacher picked along with the sections', async () => {
    vi.mocked(api.generateModulesFromSections).mockResolvedValue({ jobs: [] })
    renderTree({ modelAlias: 'groq-pro' })
    await screen.findByText(/Chapter 1/)

    await userEvent.click(checkbox(/Chapter 1/))
    await userEvent.click(generateButton())
    expect(api.generateModulesFromSections).not.toHaveBeenCalled()
    await userEvent.selectOptions(screen.getByLabelText(/Ngôn ngữ đầu ra/), 'en')
    await userEvent.type(screen.getByLabelText(/Hướng dẫn thêm/), 'Explain IDS')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

    await waitFor(() =>
      expect(api.generateModulesFromSections).toHaveBeenCalledWith('doc-1', ['s1'], 'groq-pro', 'en', 'Explain IDS'),
    )
  })

  it('sends the picked sections in document order and reports back', async () => {
    vi.mocked(api.generateModulesFromSections).mockResolvedValue({ jobs: [] })
    const { onGenerated } = renderTree()
    await screen.findByText(/Chapter 1/)

    await userEvent.click(checkbox(/Chapter 2/))
    await userEvent.click(checkbox(/Chapter 1/))
    await userEvent.click(generateButton())
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

    // Chưa chọn model thì gửi chuỗi rỗng, và lớp api bỏ hẳn khóa model_alias đi.
    await waitFor(() => expect(api.generateModulesFromSections).toHaveBeenCalledWith('doc-1', ['s1', 's3'], '', 'vi', ''))
    await waitFor(() => expect(onGenerated).toHaveBeenCalled())
    expect(generateButton()).toBeDisabled() // the selection was cleared
  })

  it('shows the modules already generated for a section as links', async () => {
    const modules: ModuleSummary[] = [
      { id: 'm1', title: 'Merge sort', learning_objective: null, bloom_level: null, is_control: true, component_count: 6, section_id: 's3' },
      { id: 'm2', title: 'Whole book module', learning_objective: null, bloom_level: null, is_control: false, component_count: 6, section_id: null },
    ]
    renderTree({ modules })

    const link = await screen.findByRole('link', { name: /Merge sort/ })

    expect(link).toHaveAttribute('href', '/modules/m1')
    expect(screen.getByText('Đối chứng (không RAG) - chỉ dùng cho nghiên cứu')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Whole book module/ })).not.toBeInTheDocument()
  })

  it('marks a section that is being generated and does not let it be picked again', async () => {
    vi.mocked(api.listSections).mockResolvedValue([section('s1', 'Chapter 1', null, { generating: true })])
    renderTree()

    expect(await screen.findByText(/Đang sinh/)).toBeInTheDocument()
    expect(checkbox(/Chapter 1/)).toBeDisabled()
  })

  it("shows the server's explanation when it refuses the request", async () => {
    vi.mocked(api.generateModulesFromSections).mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: 'Có mục đang sinh mô-đun' } },
      }),
    )
    renderTree()
    await screen.findByText(/Chapter 1/)

    await userEvent.click(checkbox(/Chapter 2/))
    await userEvent.click(generateButton())
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

    expect(await screen.findByText('Có mục đang sinh mô-đun')).toBeInTheDocument()
  })

  it('says so when the outline cannot be loaded', async () => {
    vi.mocked(api.listSections).mockRejectedValue(new Error('network'))
    renderTree()

    expect(await screen.findByText(/không tải được mục lục/i)).toBeInTheDocument()
  })

  it('keeps polling while a section is generating and stops showing it once it is done', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listSections)
      .mockResolvedValueOnce([section('s1', 'Chapter 1', null, { generating: true })])
      .mockResolvedValue([section('s1', 'Chapter 1', null, { module_count: 1 })])
    renderTree()
    expect(await screen.findByText(/Đang sinh/)).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })

    await waitFor(() => expect(screen.queryByText(/Đang sinh/)).not.toBeInTheDocument())
    expect(checkbox(/Chapter 1/)).toBeEnabled()
  })

  it('reloads the outline when the generation state of the document changes', async () => {
    const { rerenderWith } = renderTree({ documentGenerating: true })
    await screen.findByText(/Chapter 1/)
    expect(api.listSections).toHaveBeenCalledTimes(1)

    rerenderWith({ documentGenerating: false })

    await waitFor(() => expect(api.listSections).toHaveBeenCalledTimes(2))
  })
})
