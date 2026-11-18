import { useEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router-dom";

// How long a page that is still loading its code has to show its heading.
const WAIT_FOR_HEADING_MS = 3000;

// A link that takes the reader to another page leaves the page with it, so the focus falls to the
// body and a screen reader says nothing of where it now is. The new page's heading takes the focus
// instead, and is read out. The first page is left alone, as is a page that focused something itself.
export function PageChange() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const focusHeading = () => {
      const heading = document.querySelector<HTMLElement>("main h1, h1");
      if (!heading) return false;
      if (document.activeElement === document.body || !document.activeElement) {
        heading.tabIndex = -1;
        heading.focus();
      }
      return true;
    };
    if (focusHeading()) return;
    const observer = new MutationObserver(() => { if (focusHeading()) observer.disconnect(); });
    observer.observe(document.body, { childList: true, subtree: true });
    const timer = setTimeout(() => observer.disconnect(), WAIT_FOR_HEADING_MS);
    return () => { observer.disconnect(); clearTimeout(timer); };
  }, [pathname]);
  return <Outlet />;
}
