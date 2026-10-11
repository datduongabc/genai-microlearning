# Inline Content citations: private live proof, 2026-10-11

Content now keeps literal `[S#]` markers and the server-owned map for the prompt that produced them. Each physical chunk, including neighbours added by parent expansion, has one code. The model schema contains only `content`; it supplies no copied claim text, locator or URL. Saved marker spans, preview citations and study citations are derived by the server. Unknown and malformed codes are rejected. Old modules without a source map still use the legacy citation display and resolver.

The private proof used PostgreSQL/pgvector in `fm-r49-db` on port 55449, an isolated gateway in `fm-r49-gateway` on port 40449, this worktree's backend on port 8049 and its Vite server on an assigned private localhost port (36855 for the recorded browser check). The served free-tier model was **gemini-3.5-flash-lite**, through the `gemini-fast` alias pinned in experiment mode. Calls were paced to respect the provider's requests-per-minute limit. No production services, data or browser tabs were used.

For repeatable parent expansion, the live driver fixed retrieval to the first physical chunk, and question retrieval to the three fixture chunks. Planner, learning-objective generation, relevance checking, Content, slides, examples and questions used real LLM responses; Content generation and parent expansion were the production implementations. This proves citation provenance, rather than embedding or reranking quality.

The generated module was `17b0e087-363f-4b9b-8872-c20d03fd399d`. Child `fd8b63b9-d8e9-4e40-b062-7da2b5a8f35f` expanded to three separate evidence chunks. The model emitted five markers with source codes `S1, S1, S2, S3, S3`. In the preview these rendered as **[1], [1], [2], [3], [3]**. Clicking [2] opened neighbour `7a9d5a66-5ad0-474a-a3ea-9be0c021bf14`, which was not in the original child selection, with this exact prompted text:

> The insertion process visits at most the height of the binary heap. A binary heap with n elements has height O(log n), so its worst-case insertion time is O(log n). This cost depends on the path to the root, not on scanning every element.

The saved citation and exported study evidence both resolved to that physical chunk with identical text. An additional real call generated Vietnamese Content with inline codes. A real no-RAG control generation kept citations empty. A real chat-edit call shortened the saved Content to one sourced bullet and re-derived its claim span and exact chunk identity.

The browser bridge was unavailable, so firstmate authorized one direct CDP attempt against a private native Chrome instance on port 9249. That attempt opened a new tab, checked all five numbered buttons, opened the correct source text, and verified Escape dismissal. The additional manual-edit scenario then aborted when the test script asked CDP to serialize a DOM element (`Object reference chain is too long`). No browser retry was made. Manual removal, unknown-code rejection and source-map protection were verified through real HTTP requests and frontend integration tests instead. After an earlier private-stack cleanup, the exact saved real-LLM snapshot was restored for this browser attempt; it was not a new model output.

Local artifacts and reproduction drivers are under `/mnt/d/firstmate/data/do-an-r49-inline-citations/`. Raw responses and screenshots stay outside the repository; temporary credential files were removed. Every private process/container started for the proof is removed after validation.

| Test / scenario | Result | Evidence |
| --- | --- | --- |
| Backend automated suite | 1,180 passed; 1 live test deselected | `backend-final-verified.log` |
| Frontend automated suite | 239 passed | `frontend-final.log` |
| Frontend production build and lint | Passed | `npm run build`, `npm run lint` |
| Backend lint and whitespace validation | Passed | Ruff, `git diff --check` |
| Real English module, expanded neighbours and exact evidence | Passed; five markers, two added neighbours | `live-generation.log`, `live-module.json`, `llm-call-*.json` |
| Vietnamese Content and no-RAG control | Passed | `live-content-vi.json`, `live-control.json` |
| Preview numbered buttons, exact source and Escape | Passed | `browser-proof.json`, `preview-inline.png`, `preview-source-S2.png` |
| Extended manual browser editing | CDP serialization gap; no retry | `browser-proof.log`; frontend and HTTP coverage used |
| Manual stale-citation removal and protected server map | Passed | `live-edit-proof.json` |
| Unknown source code | HTTP 422, saved text unchanged | `live-edit-proof.json` |
| Real chat Content edit | Passed; current marker derives current claim and exact chunk | `live-edit-proof.json` |
| Study packet evidence and independent claim extraction | Exact snapshot verified; automated judge-path tests passed | `live-study.json`, citation/claim evaluation tests |

The Test pipeline should run automated suites plus one short smoke check of the changed flow. The live scenarios above have already been driven and recorded; a second full private-stack/model run is unnecessary. The manual-edit `ai_original` issue remains outside this change.
