// Every length is floored, as the native player floors its clock: rounding showed one video as 4:10
// in the learner list and 4:09 in the player.

// Minutes with at most one decimal, written the Vietnamese way (4,9), as the server words its limit.
export function formatMinutes(seconds: number): string {
  return (Math.floor(seconds / 6) / 10).toLocaleString('vi-VN', { maximumFractionDigits: 1 })
}

// A position in a video as a player shows it: 2:05.
export function formatClock(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
