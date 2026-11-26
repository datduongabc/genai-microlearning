import { describe, expect, it } from 'vitest'
import { formatClock, formatMinutes } from '../../../lib/duration'
import { initials } from '../../../lib/initials'

describe('initials', () => {
  it('takes the first letters of the first and the last word', () => {
    expect(initials('Lê Minh Quân')).toBe('LQ')
    expect(initials(' Nguyễn  An ')).toBe('NA')
  })

  it('takes one letter from a single word', () => {
    expect(initials('Admin')).toBe('A')
  })

  it('uppercases lowercase names', () => {
    expect(initials('lê minh quân')).toBe('LQ')
  })
})

// Live run 2 showed one video as 4:10 in the learner list and 4:09 in the player, which floors.
describe('duration', () => {
  it('floors a clock as the native player does', () => {
    expect(formatClock(249.583)).toBe('4:09')
    expect(formatClock(30.5)).toBe('0:30')
    expect(formatClock(59.999)).toBe('0:59')
    expect(formatClock(0)).toBe('0:00')
  })

  it('floors minutes too, so they never read longer than the clock', () => {
    expect(formatMinutes(249.583)).toBe('4,1')
    expect(formatMinutes(291)).toBe('4,8')
    expect(formatMinutes(480)).toBe('8')
  })
})
