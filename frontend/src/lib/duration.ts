// Minutes with at most one decimal, written the Vietnamese way (4,9), as the server words its limit.
export function formatMinutes(seconds: number): string {
  return (seconds / 60).toLocaleString('vi-VN', { maximumFractionDigits: 1 })
}

// A position in a video as a player shows it: 2:05.
export function formatClock(seconds: number): string {
  const whole = Math.round(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
