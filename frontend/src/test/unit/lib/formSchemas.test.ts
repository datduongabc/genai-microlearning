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

const MB = 1024 * 1024
const fileOfSize = (name: string, size: number) => {
  const file = new File(['x'], name)
  Object.defineProperty(file, 'size', { value: size })
  return file
}

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
