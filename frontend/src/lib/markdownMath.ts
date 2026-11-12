// Formulas in generated text and chat answers. The models write them as LaTeX between dollar
// signs ($M = (K, \Sigma, \delta, s, F)$), which react-markdown alone shows as raw source.
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import type { Options } from 'react-markdown'

export const mathRemarkPlugins: Options['remarkPlugins'] = [remarkMath]
// throwOnError false: a formula the model got wrong is shown as its source in red, not as an
// error that blanks the whole answer.
export const mathRehypePlugins: Options['rehypePlugins'] = [[rehypeKatex, { throwOnError: false }]]
