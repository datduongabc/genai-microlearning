const units = ["B", "KB", "MB", "GB"];

// "1,2 MB": one decimal below 10 of a unit, the way a file manager shows it, in Vietnamese notation.
export function formatSize(bytes: number) {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  const digits = unit > 0 && value < 10 ? 1 : 0;
  return `${value.toLocaleString("vi-VN", { maximumFractionDigits: digits, minimumFractionDigits: digits })} ${units[unit]}`;
}
