import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { LearnItems } from '../../../components/LearnItems'
import * as api from '../../../api'

vi.mock('../../../api')

const document = { id: 'd1', filename: 'Chapter 5.pdf' }
const set = (id: string, focus: string | null, day: string) =>
  ({ id, focus, card_count: 8, published_at: `2026-11-${day}T08:00:00Z`, document })

describe('LearnItems', () => {
  it('leads with the newest delivery of each focus and folds the earlier ones under it', async () => {
    vi.mocked(api.listLearnItems).mockResolvedValue({
      flashcard_sets: [set('a', 'FCFS và Round Robin', '13'), set('b', 'FCFS và Round Robin ', '16'),
        set('c', 'fcfs và round robin', '14'), set('d', 'Deadlock', '12')],
      videos: [],
    } as never)

    render(<MemoryRouter><LearnItems courseId="c1" /></MemoryRouter>)

    const list = (await screen.findAllByRole('list'))[0]
    const rows = within(list).getAllByRole('listitem').filter((li) => li.parentElement === list)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getAllByRole('link')[0]).toHaveAttribute('href', '/learn/c1/flashcards/b')
    expect(within(rows[0]).getByText('2 bản giao trước')).toBeInTheDocument()
    const earlier = within(rows[0]).getAllByRole('link', { hidden: true }).slice(1).map((a) => a.getAttribute('href'))
    expect(earlier).toEqual(['/learn/c1/flashcards/c', '/learn/c1/flashcards/a'])
    expect(within(rows[1]).queryByText(/bản giao trước/)).not.toBeInTheDocument()
  })
})
