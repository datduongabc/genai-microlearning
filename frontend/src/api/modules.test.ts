import { afterEach, expect, it, vi } from 'vitest'
import { api } from './client'
import { generateModule, generateModulesFromSections } from './modules'

afterEach(() => vi.restoreAllMocks())

it('sends the selected output language for whole documents and sections', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  await generateModule('doc', null, 'en')
  expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'en' }, { params: { document_id: 'doc' } })
  await generateModulesFromSections('doc', ['section'], 'gemini-fast', 'en')
  expect(post).toHaveBeenLastCalledWith('/modules/generate', { section_ids: ['section'], model_alias: 'gemini-fast', output_language: 'en' }, { params: { document_id: 'doc' } })
  await generateModule('doc')
  expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'vi' }, { params: { document_id: 'doc' } })
})
