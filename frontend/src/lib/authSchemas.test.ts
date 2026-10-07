import { describe, expect, it } from 'vitest'
import { loginSchema } from './authSchemas'

describe('loginSchema', () => {
  it('accepts a Moodle username and a password', () => {
    expect(loginSchema.safeParse({ username: 'lan.nguyen', password: 'password123' }).success).toBe(true)
  })

  it('accepts an email as the username, which is how an admin signs in', () => {
    expect(loginSchema.safeParse({ username: 'admin@school.edu.vn', password: 'password123' }).success).toBe(true)
  })

  it('rejects an empty or blank username', () => {
    expect(loginSchema.safeParse({ username: '', password: 'password123' }).success).toBe(false)
    expect(loginSchema.safeParse({ username: '   ', password: 'password123' }).success).toBe(false)
  })

  it('rejects an empty password', () => {
    expect(loginSchema.safeParse({ username: 'lan.nguyen', password: '' }).success).toBe(false)
  })

  // The rules for choosing a password are Moodle's (and the create-admin command's). An account made
  // under older rules (a short password, or one over 72 characters) must still be able to sign in.
  it.each([
    ['a short one', 'short'],
    ['a long one', 'a'.repeat(200)],
  ])('lets an account with %s sign in, the server decides', (_name, password) => {
    expect(loginSchema.safeParse({ username: 'lan.nguyen', password }).success).toBe(true)
  })

  it('rejects a password no client would send, the same size the server refuses', () => {
    expect(loginSchema.safeParse({ username: 'lan.nguyen', password: 'a'.repeat(1025) }).success).toBe(false)
  })
})
