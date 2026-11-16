import { useEffect, useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";

// A textarea as tall as its text. With fixed rows, a flashcard's answer at 390 px showed its fourth
// line cut in half and the rest behind an inner scroll, so the lecturer could not read it at a glance.
export function AutoGrowTextarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);

  const fit = () => {
    const element = ref.current;
    if (!element) return;
    // Back to the rows' height first, or a box never shrinks when text is removed.
    element.style.height = "auto";
    const style = getComputedStyle(element);
    // border-box sizing: scrollHeight counts the padding but not the borders.
    const borders = (parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)) || 0;
    element.style.height = `${element.scrollHeight + borders}px`;
  };

  useLayoutEffect(fit, [props.value]);

  // The text wraps again at another width: a phone turned, a window resized.
  useEffect(() => {
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  return <textarea ref={ref} className={`auto-grow ${className ?? ""}`} {...props} />;
}
