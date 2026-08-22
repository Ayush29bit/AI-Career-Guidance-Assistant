/**
 * The message input.
 *
 * Enter sends, Shift+Enter breaks a line -- the convention people already have
 * from every chat client, so it needs no hint beyond the small one under the
 * field. The textarea grows with the content up to a ceiling, then scrolls;
 * a fixed single-line input would hide the middle of a long message while
 * writing it.
 *
 * Sending is blocked while a turn is in flight. The backend would accept a
 * second message, but the counsellor would then be answering two turns at once
 * and the replies would arrive interleaved.
 */

import { useEffect, useRef, useState } from 'react'

const MAX_LENGTH = 4000 // matches the backend's own cap on message content
const MAX_HEIGHT = 160

interface ComposerProps {
  onSend: (content: string) => void
  disabled: boolean
  sending: boolean
}

export function Composer({ onSend, disabled, sending }: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Grow to fit, up to the ceiling. Reset to `auto` first so the field also
  // shrinks back when text is deleted.
  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`
  }, [value])

  // Return focus once the counsellor has replied, so the next message can be
  // typed without reaching for the mouse.
  useEffect(() => {
    if (!disabled) textareaRef.current?.focus()
  }, [disabled])

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
  }

  const canSend = value.trim().length > 0 && !disabled

  return (
    <div className="border-t border-slate-200 bg-white/80 px-4 py-3 backdrop-blur-sm sm:px-6 sm:py-4">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors focus-within:border-brand-600 focus-within:ring-1 focus-within:ring-brand-600">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            maxLength={MAX_LENGTH}
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder="Tell me what you're studying, what you enjoy, or what you're unsure about…"
            aria-label="Message the counsellor"
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            aria-label="Send message"
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
          >
            {sending ? (
              <svg viewBox="0 0 24 24" className="size-4 animate-spin" fill="none" strokeWidth="2.5">
                <circle cx="12" cy="12" r="9" stroke="currentColor" opacity="0.3" />
                <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeLinecap="round" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" className="size-4" fill="none" strokeWidth="2">
                <path
                  d="M5 12h13M12 5l7 7-7 7"
                  stroke="currentColor"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </button>
        </div>
        <p className="mt-2 px-1 text-xs text-slate-400">
          Enter to send · Shift + Enter for a new line
        </p>
      </div>
    </div>
  )
}
