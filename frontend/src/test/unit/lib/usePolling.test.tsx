import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling } from '../../../lib/usePolling'

describe('usePolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('never applies an older answer after a newer one when the server is slow', async () => {
    // The first read takes longer than the interval; a second one started meanwhile would answer
    // first, and the first would then put the older status back.
    const answers: Array<(value: string) => void> = []
    const load = vi.fn(() => new Promise<string>((resolve) => { answers.push(resolve) }))
    const applied: string[] = []
    renderHook(() => usePolling(true, load, (value: string) => { applied.push(value) }, 1000))

    await act(async () => { vi.advanceTimersByTime(1000) })
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(load).toHaveBeenCalledTimes(1)

    await act(async () => { answers[0]('processing') })
    await act(async () => { vi.advanceTimersByTime(1000) })
    await act(async () => { answers[1]('embedded') })
    expect(applied).toEqual(['processing', 'embedded'])
  })

  it('stops reading once it is no longer active', async () => {
    const load = vi.fn(async () => 'x')
    const { rerender } = renderHook(({ active }) => usePolling(active, load, () => {}, 1000),
      { initialProps: { active: true } })
    await act(async () => { vi.advanceTimersByTime(1000) })
    rerender({ active: false })
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(load).toHaveBeenCalledTimes(1)
  })
})
