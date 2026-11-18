import { useEffect } from "react";

// "<page> - GENAILMS", the first thing a screen reader says of a tab or a page it comes to.
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} - GENAILMS` : "GENAILMS";
  }, [title]);
}
