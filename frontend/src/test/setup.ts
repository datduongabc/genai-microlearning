import '@testing-library/jest-dom/vitest'

// React 19 + Vitest doesn't set this automatically -- without it, state
// updates inside act() still print "not configured to support act(...)"
// warnings even though act() is being used correctly.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// jsdom has no native modal API; browser validation covers focus and Escape.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
