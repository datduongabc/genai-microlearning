import { afterEach, expect, it, vi } from 'vitest'
import { api } from './client'
import { deleteModule, generateModule, generateModulesFromSections } from './modules'

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

it('sends trimmed guidance with the selected language for documents and sections', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  await generateModule('doc', null, 'en', '  Explain IDS  ')
  expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'en', custom_instructions: 'Explain IDS' }, { params: { document_id: 'doc' } })
  await generateModulesFromSections('doc', ['section'], null, 'vi', 'Explain DLDFS')
  expect(post).toHaveBeenLastCalledWith('/modules/generate', { section_ids: ['section'], output_language: 'vi', custom_instructions: 'Explain DLDFS' }, { params: { document_id: 'doc' } })
})

it('deletes a module by id', async () => {
  const del = vi.spyOn(api, 'delete').mockResolvedValue({ data: { status: 'deleted', deleted_module_ids: ['m'] } })
  await deleteModule('m')
  expect(del).toHaveBeenCalledWith('/modules/m')
})
