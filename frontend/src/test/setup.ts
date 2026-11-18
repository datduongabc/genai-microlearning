import '@testing-library/jest-dom/vitest'

// React 19 + Vitest doesn't set this automatically -- without it, state
// updates inside act() still print "not configured to support act(...)"
// warnings even though act() is being used correctly.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// jsdom has no native modal API; browser validation covers focus and Escape.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }

// Radix menus open on a primary-button pointerdown and measure themselves; jsdom has neither.
if (!window.PointerEvent) {
  class PointerEvent extends MouseEvent {
    pointerId: number; pointerType: string
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; this.pointerType = init.pointerType ?? 'mouse' }
  }
  window.PointerEvent = PointerEvent as unknown as typeof window.PointerEvent
}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
Element.prototype.scrollIntoView ??= () => {}
window.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
