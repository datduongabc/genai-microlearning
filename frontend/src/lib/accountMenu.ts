export const OPEN_ACCOUNT_MENU_EVENT = "open-account-menu";

export function openAccountMenu() {
  window.dispatchEvent(new Event(OPEN_ACCOUNT_MENU_EVENT));
}
