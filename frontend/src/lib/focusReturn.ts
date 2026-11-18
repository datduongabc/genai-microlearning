// Where focus should go when a dialog opened from here closes. A menu item that opened it is gone
// by then (focus would fall to the page body), so its menu's button; otherwise whatever has focus.
export function focusReturnTarget(): HTMLElement | null {
  const active = document.activeElement as HTMLElement | null;
  const menu = active?.closest("[role=menu]");
  return (menu && document.getElementById(menu.getAttribute("aria-labelledby") ?? "")) || active;
}
