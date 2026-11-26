import { describe, expect, it } from 'vitest'
import {
  chatEditSchema,
  createCourseSchema,
  generateModuleSchema,
  linkMoodleCourseSchema,
  moduleTitleSchema,
  publishToMoodleSchema,
  questionEditSchema,
  uploadDocumentSchema,
  uploadSizeLimit,
} from '../../../lib/formSchemas'
import { loginSchema, registerSchema } from '../../../lib/authSchemas'
import { emptyRating, quizQualityScore, raterEmailsSchema, ratingSchema } from '../../../lib/ratingSchema'

const MB = 1024 * 1024
const fileOfSize = (name: string, size: number) => {
  const file = new File(['x'], name)
  Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('blind rubric validation', () => {
  it('requires all nine integer scores and preserves findings and overall notes', () => {
    const body = emptyRating('M01')
    expect(ratingSchema.safeParse(body).success).toBe(false)
    for (const key of [
      'accuracy',
      'completeness',
      'consistency',
      'hallucination_score',
      'slide_quality',
      'quiz_quality',
      'bloom_fidelity',
      'query_alignment',
      'examples_feedback',
    ] as const) {
      body[key] = { score: 4, justification: 'Có căn cứ' }
    }
    body.hallucination_findings = [
      { claim: 'Một câu', field: 'content', reason: 'Không có trong nguồn' },
    ]
    body.overall_notes = 'Ghi chú'
    expect(ratingSchema.parse(body)).toEqual(body)
    for (const score of [0, 6, 2.5, NaN]) {
      expect(
        ratingSchema.safeParse({ ...body, accuracy: { score, justification: 'x' } }).success,
      ).toBe(false)
    }
  })

  it('scores the quiz like the server: the share of questions with at most one flaw', () => {
    const flawed = (n: number) => Array.from({ length: 6 }, (_, i) =>
      ({ question: i + 1, flaws: i < n ? ['vague_terms', 'lost_sequence'] : ['vague_terms'] }))
    // The same values backend/app/generation/evaluation.py gives for six questions.
    expect([0, 1, 2, 3, 4, 5, 6].map((n) => quizQualityScore(flawed(n)))).toEqual([5, 4, 4, 3, 2, 2, 1])
    expect(quizQualityScore([{ question: 1, flaws: ['vague_terms', 'vague_terms'] }])).toBe(5)
  })

  it('accepts an empty invitation list and rejects malformed emails', () => {
    expect(raterEmailsSchema.parse({ emails: [] })).toEqual({ emails: [] })
    expect(raterEmailsSchema.safeParse({ emails: ['bad'] }).success).toBe(false)
  })
})

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

  // The rules for choosing a password belong to signing up. An account made under older
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

describe('registerSchema', () => {
  const base = { name: 'Nguyen Van A', email: 'a@b.com', password: 'password123' }
  const parse = (changes: object) => registerSchema.safeParse({ ...base, confirmPassword: 'password123', ...changes })
  const paths = (result: ReturnType<typeof parse>) => (result.success ? [] : result.error.issues.map((i) => i.path[0]))

  it('accepts matching passwords', () => {
    const result = registerSchema.safeParse({ ...base, confirmPassword: 'password123' })
    expect(result.success).toBe(true)
  })

  it('rejects mismatched confirmPassword and attaches the error to that field', () => {
    const result = registerSchema.safeParse({ ...base, confirmPassword: 'different123' })
    expect(result.success).toBe(false)
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes('confirmPassword'))).toBe(true)
    }
  })

  it('rejects an empty name', () => {
    const result = registerSchema.safeParse({ ...base, name: '', confirmPassword: 'password123' })
    expect(result.success).toBe(false)
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
    expect(paths(parse({ password: 'short', confirmPassword: 'short' }))).toEqual(['password'])
  })

  it('counts a limit of 72 bytes, not characters, because that is what the server hashes', () => {
    const ascii = 'a'.repeat(72)
    // 25 letters of 3 bytes each: 25 characters, 75 bytes.
    const vietnamese = 'ệ'.repeat(25)

    expect(parse({ password: ascii, confirmPassword: ascii }).success).toBe(true)
    expect(paths(parse({ password: 'a'.repeat(73), confirmPassword: 'a'.repeat(73) }))).toEqual(['password'])
    expect(new TextEncoder().encode(vietnamese).length).toBeGreaterThan(72)
    expect(paths(parse({ password: vietnamese, confirmPassword: vietnamese }))).toEqual(['password'])
  })
})


describe('createCourseSchema', () => {
  it('trims the name', () => {
    expect(createCourseSchema.parse({ name: '  AI 101  ' })).toEqual({ name: 'AI 101' })
  })

  it('asks for a name', () => {
    const result = createCourseSchema.safeParse({ name: '   ' })
    expect(result.error?.issues[0].message).toBe('Vui lòng nhập tên môn học')
  })

  it('stops at the 200 characters the server takes', () => {
    expect(createCourseSchema.safeParse({ name: 'a'.repeat(200) }).success).toBe(true)
    expect(createCourseSchema.safeParse({ name: 'a'.repeat(201) }).success).toBe(false)
  })
})

describe('generateModuleSchema', () => {
  it('trims the guidance and keeps the language', () => {
    expect(generateModuleSchema.parse({ language: 'en', instructions: '  Explain IDS ' })).toEqual({
      language: 'en',
      instructions: 'Explain IDS',
    })
  })

  it('accepts no guidance at all', () => {
    expect(generateModuleSchema.safeParse({ language: 'vi', instructions: '' }).success).toBe(true)
  })

  it('rejects a language other than vi or en', () => {
    expect(generateModuleSchema.safeParse({ language: 'fr', instructions: '' }).success).toBe(false)
  })

  it('stops at 1000 characters', () => {
    expect(generateModuleSchema.safeParse({ language: 'vi', instructions: 'a'.repeat(1001) }).success).toBe(false)
  })
})

describe('uploadDocumentSchema', () => {
  it('takes a file within the limit of its kind', () => {
    expect(uploadDocumentSchema.safeParse({ file: fileOfSize('a.pdf', 25 * MB) }).success).toBe(true)
    expect(uploadDocumentSchema.safeParse({ file: fileOfSize('a.mp3', 200 * MB) }).success).toBe(true)
    expect(uploadDocumentSchema.safeParse({ file: fileOfSize('a.mp4', 1024 * MB) }).success).toBe(true)
  })

  it.each([
    ['a.pdf', 25 * MB + 1, 'Tài liệu tối đa 25 MB'],
    ['a.MP3', 200 * MB + 1, 'Tệp âm thanh tối đa 200 MB'],
    ['a.mp4', 1024 * MB + 1, 'Tệp video tối đa 1 GB'],
  ])('says why %s of %d bytes is too big', (name, size, message) => {
    const result = uploadDocumentSchema.safeParse({ file: fileOfSize(name, size) })
    expect(result.error?.issues[0].message).toBe(message)
  })

  it('asks for a file', () => {
    expect(uploadDocumentSchema.safeParse({}).success).toBe(false)
  })

  it('finds the limit of a name without an extension', () => {
    expect(uploadSizeLimit('README').bytes).toBe(25 * MB)
  })
})

describe('linkMoodleCourseSchema', () => {
  it('asks for a course to be chosen', () => {
    const result = linkMoodleCourseSchema.safeParse({ moodleCourseId: '' })
    expect(result.error?.issues[0].message).toBe('Vui lòng chọn một khóa học Moodle')
    expect(linkMoodleCourseSchema.safeParse({ moodleCourseId: '6' }).success).toBe(true)
  })
})

describe('publishToMoodleSchema', () => {
  it('takes a section number the server accepts', () => {
    expect(publishToMoodleSchema.safeParse({ sectionNum: 0, addNewSection: false }).success).toBe(true)
    expect(publishToMoodleSchema.safeParse({ sectionNum: 1000, addNewSection: true }).success).toBe(true)
    expect(publishToMoodleSchema.safeParse({ sectionNum: -1, addNewSection: false }).success).toBe(false)
    expect(publishToMoodleSchema.safeParse({ sectionNum: 1001, addNewSection: false }).success).toBe(false)
    expect(publishToMoodleSchema.safeParse({ sectionNum: Number.NaN, addNewSection: false }).success).toBe(false)
  })
})

describe('chatEditSchema', () => {
  it('trims the request', () => {
    expect(chatEditSchema.parse({ message: '  Rút gọn slide 1 ' })).toEqual({ message: 'Rút gọn slide 1' })
  })

  it('asks for a request', () => {
    expect(chatEditSchema.safeParse({ message: '  ' }).error?.issues[0].message).toBe(
      'Vui lòng nhập yêu cầu chỉnh sửa',
    )
  })

  it('stops at 4000 characters', () => {
    expect(chatEditSchema.safeParse({ message: 'a'.repeat(4000) }).success).toBe(true)
    expect(chatEditSchema.safeParse({ message: 'a'.repeat(4001) }).success).toBe(false)
  })
})

describe('moduleTitleSchema', () => {
  it('stops at the 200 characters the server takes', () => {
    expect(moduleTitleSchema.safeParse({ title: 'a'.repeat(200) }).success).toBe(true)
    expect(moduleTitleSchema.safeParse({ title: 'a'.repeat(201) }).success).toBe(false)
  })
})

describe('questionEditSchema', () => {
  const valid = { stem: 'Q', choices: ['A', 'B'], answer_key: '1', explanation: '' }

  it('turns the chosen answer into a number', () => {
    expect(questionEditSchema.parse(valid)).toEqual({ ...valid, answer_key: 1 })
  })

  it('asks for the question and for every choice', () => {
    expect(questionEditSchema.safeParse({ ...valid, stem: '' }).error?.issues[0].message).toBe(
      'Vui lòng nhập câu hỏi',
    )
    expect(questionEditSchema.safeParse({ ...valid, choices: ['A', ''] }).error?.issues[0].message).toBe(
      'Vui lòng nhập phương án',
    )
  })

  it('needs at least two choices and a numeric answer', () => {
    expect(questionEditSchema.safeParse({ ...valid, choices: ['A'] }).success).toBe(false)
    expect(questionEditSchema.safeParse({ ...valid, answer_key: 'x' }).success).toBe(false)
  })
})
