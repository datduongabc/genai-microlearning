import type { Plugin } from 'vite'

// KaTeX and Fontsource list .woff and .ttf fallbacks after .woff2. Every browser the build targets
// reads woff2, so the fallbacks would only add dozens of files nobody downloads to every deploy.
export function woff2Only(): Plugin {
  return {
    name: 'woff2-only',
    enforce: 'pre',
    transform(code, id) {
      if (!/node_modules\/(katex|@fontsource)\/.*\.css$/.test(id)) return null
      return code.replace(/,\s*url\([^)]*\.(woff|ttf)\)\s*format\(["'](woff|truetype)["']\)/g, '')
    },
  }
}
