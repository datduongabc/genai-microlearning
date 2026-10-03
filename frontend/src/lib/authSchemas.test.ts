import { describe, expect, it } from 'vitest'
import { createLecturerSchema, loginSchema, resetPasswordSchema } from './authSchemas'

describe('loginSchema', () => {
  it('accepts a valid email and password', () => {
    const result = loginSchema.safeParse({ email: 'a@b.com', password: 'password123' })
    expect(result.success).toBe(true)
  })

  it('rejects an invalid email', () => {
    const result = loginSchema.safeParse({ email: 'not-an-email', password: 'password123' })
    expect(result.success).toBe(false)
  })

  it('rejects an empty password', () => {
    expect(loginSchema.safeParse({ email: 'a@b.com', password: '' }).success).toBe(false)
  })

  // The rules for choosing a password belong to creating an account. An account made under older
  // rules (a short password, or one over 72 characters) must still be able to sign in.
  it.each([
    ['a short one', 'short'],
    ['a long one', 'a'.repeat(200)],
  ])('lets an account with %s sign in, the server decides', (_name, password) => {
    expect(loginSchema.safeParse({ email: 'a@b.com', password }).success).toBe(true)
  })

  it('rejects a password no client would send, the same size the server refuses', () => {
    expect(loginSchema.safeParse({ email: 'a@b.com', password: 'a'.repeat(1025) }).success).toBe(false)
  })
})

describe('createLecturerSchema', () => {
  const base = { name: 'Nguyen Van A', email: 'a@b.edu', password: 'password123' }
  const parse = (changes: object) => createLecturerSchema.safeParse({ ...base, ...changes })
  const paths = (result: ReturnType<typeof parse>) => (result.success ? [] : result.error.issues.map((i) => i.path[0]))

  it('accepts a valid name, email and password', () => {
    expect(parse({}).success).toBe(true)
  })

  it('rejects an invalid email', () => {
    expect(paths(parse({ email: 'not-an-email' }))).toEqual(['email'])
  })

  it('accepts the edu addresses of schools, as the server does', () => {
    for (const email of ['a@hcmut.edu.vn', 'a@x.edu', 'a@Mail.HCMUT.EDU.VN']) {
      expect(parse({ email }).success, email).toBe(true)
    }
  })

  it('rejects an address that is not at an edu domain, with the reason', () => {
    for (const email of ['a@gmail.com', 'a@example.com', 'a@edu.com', 'a@x.edu.com']) {
      const result = parse({ email })
      expect(paths(result), email).toEqual(['email'])
      expect(!result.success && result.error.issues[0].message).toMatch(/email của trường/)
    }
  })

  it('rejects an empty name', () => {
    expect(parse({ name: '' }).success).toBe(false)
  })

  it('rejects a name of nothing but spaces, which the server would refuse', () => {
    expect(paths(parse({ name: '    ' }))).toEqual(['name'])
  })

  it('trims the name before it is sent', () => {
    const result = parse({ name: '  Nguyen Van A  ' })

    expect(result.success && result.data.name).toBe('Nguyen Van A')
  })

  it('accepts a name of 100 characters and rejects 101', () => {
    expect(parse({ name: 'n'.repeat(100) }).success).toBe(true)
    expect(paths(parse({ name: 'n'.repeat(101) }))).toEqual(['name'])
  })

  it('rejects a password shorter than 8 characters', () => {
    expect(paths(parse({ password: 'short' }))).toEqual(['password'])
  })

  it('counts a limit of 72 bytes, not characters, because that is what the server hashes', () => {
    const ascii = 'a'.repeat(72)
    // 25 letters of 3 bytes each: 25 characters, 75 bytes.
    const vietnamese = 'ệ'.repeat(25)

    expect(parse({ password: ascii }).success).toBe(true)
    expect(paths(parse({ password: 'a'.repeat(73) }))).toEqual(['password'])
    expect(new TextEncoder().encode(vietnamese).length).toBeGreaterThan(72)
    expect(paths(parse({ password: vietnamese }))).toEqual(['password'])
  })
})

describe('resetPasswordSchema', () => {
  it('applies the same password rules', () => {
    expect(resetPasswordSchema.safeParse({ password: 'a long enough phrase' }).success).toBe(true)
    expect(resetPasswordSchema.safeParse({ password: 'short' }).success).toBe(false)
  })
})
