// First letter of the first and of the last word: "Lê Minh Quân" → "LQ".
export function initials(name: string) {
  const words = name.trim().split(/\s+/);
  return ((words[0][0] ?? "") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}
