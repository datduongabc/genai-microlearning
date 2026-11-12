// Minutes with at most one decimal, written the Vietnamese way (4,9), as the server words its limit.
export function formatMinutes(seconds: number): string {
  return (seconds / 60).toLocaleString('vi-VN', { maximumFractionDigits: 1 })
}
