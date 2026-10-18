import { describe, expect, it } from 'vitest'
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
