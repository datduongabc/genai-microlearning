import '@testing-library/jest-dom/vitest'
import { configure, render } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, beforeEach } from 'vitest'
import { Toaster, toast } from 'sonner'

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

// Every result of an action is a toast (lib/notify.ts), so every test has the app's one Toaster,
// as App.tsx mounts it. Sonner keeps its toasts in a module-level store; without the dismiss a
// toast from one test is still listed in the next.
beforeEach(() => { render(createElement(Toaster)) })
afterEach(() => { toast.dismiss() })

// findBy and waitFor give up after 1 s by default. With the whole suite sharing the machine, a
// render that takes 0.2 s alone can take longer than that, and a correct test failed now and then
// (AppShell's linked-account test, about 1 run in 8). They still return as soon as the page is right.
configure({ asyncUtilTimeout: 3000 })
