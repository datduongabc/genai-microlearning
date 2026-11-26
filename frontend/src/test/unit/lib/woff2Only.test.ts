import { describe, expect, it } from 'vitest'
import { woff2Only } from '../../../../woff2Only'

const transform = (code: string, id: string) =>
  (woff2Only().transform as (code: string, id: string) => string | null)(code, id)

describe('woff2Only', () => {
  it('drops the woff and ttf fallbacks of KaTeX and Fontsource, keeping woff2', () => {
    const katex = '@font-face{src:url(fonts/KaTeX_Main-Regular.woff2) format("woff2"),url(fonts/KaTeX_Main-Regular.woff) format("woff"),url(fonts/KaTeX_Main-Regular.ttf) format("truetype")}'
    expect(transform(katex, '/x/node_modules/katex/dist/katex.min.css')).toBe('@font-face{src:url(fonts/KaTeX_Main-Regular.woff2) format("woff2")}')
    const fontsource = "src: url(./files/a-400-normal.woff2) format('woff2'), url(./files/a-400-normal.woff) format('woff');"
    expect(transform(fontsource, '/x/node_modules/@fontsource/be-vietnam-pro/400.css')).toBe("src: url(./files/a-400-normal.woff2) format('woff2');")
  })

  it('leaves the app own stylesheets alone', () => {
    expect(transform('a{}', '/x/src/index.css')).toBeNull()
  })
})
