import { Component, type ReactNode } from 'react'

// How Chrome, Firefox, Safari and Vite's preload helper word a failed dynamic import. After a
// deploy, a tab opened before it asks for chunk names that the new build no longer has.
const CHUNK_LOAD_FAILED = /dynamically imported module|importing a module script failed|unable to preload/i

// Without a boundary React 19 unmounts the whole root on a render error and leaves a blank page.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div role="alert" className="mx-auto flex max-w-md flex-col items-start gap-4 px-4 py-12">
        <p>{CHUNK_LOAD_FAILED.test(error.message) ? 'Đã có phiên bản mới, tải lại trang.' : 'Đã có lỗi khi hiển thị trang.'}</p>
        <button type="button" className="ui-button primary" onClick={() => window.location.reload()}>
          Tải lại trang
        </button>
      </div>
    )
  }
}
