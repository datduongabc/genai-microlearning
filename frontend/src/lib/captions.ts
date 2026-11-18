// [start, end, text] in seconds of the rendered file, one per sentence of the voice.
export type CaptionCue = [number, number, string]

const stamp = (seconds: number) => new Date(Math.round(seconds * 1000)).toISOString().slice(11, 23)
// The text is the model's narration: a < or & in it would be read as WebVTT markup.
const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function webVtt(cues: CaptionCue[]): string {
  return `WEBVTT\n\n${cues.map(([start, end, text]) => `${stamp(start)} --> ${stamp(end)}\n${escape(text)}`).join('\n\n')}\n`
}

// A <track> loads only a URL, and the API wants the token in a header, so the file travels in the
// URL itself: a few kilobytes for an 8-minute video, and nothing to revoke.
export function captionsUrl(cues: CaptionCue[]): string | null {
  return cues.length ? `data:text/vtt;charset=utf-8,${encodeURIComponent(webVtt(cues))}` : null
}

export const captionsLabel = (language: string) => (language === 'en' ? 'English' : 'Tiếng Việt')
