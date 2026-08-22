/**
 * The conversation surface: history, thinking state, errors, composer.
 *
 * Auto-scroll follows the newest turn, but only when the reader is already at
 * the bottom. Scrolling someone back down while they are re-reading an earlier
 * answer is worse than not following at all.
 *
 * The welcome message is written here rather than fetched. It is the interface
 * introducing itself before any turn exists -- it makes no claim about the
 * student, so there is nothing for the backend to be the source of truth about.
 */

import { useEffect, useRef } from 'react'
import type { ChatMessage } from '../../hooks/useConversation'
import { Composer } from './Composer'
import { CounsellorMark } from './CounsellorMark'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'

/** How close to the bottom still counts as "following the conversation". */
const FOLLOW_THRESHOLD_PX = 120

interface ChatProps {
  messages: ChatMessage[]
  sending: boolean
  starting: boolean
  ready: boolean
  error: string | null
  retryable: string | null
  onSend: (content: string) => void
  onRetry: () => void
  onDismissError: () => void
}

function Welcome() {
  return (
    <div className="flex items-start gap-3">
      <CounsellorMark />
      <div className="max-w-[92%] rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:max-w-[80%]">
        <p className="text-[15px] leading-relaxed text-slate-800">
          Hi — I'm here to help you work out which career directions actually suit you.
        </p>
        <p className="mt-2.5 text-[15px] leading-relaxed text-slate-800">
          There's no form to fill in. Just tell me what you're studying, what you enjoy
          working on, or what you're unsure about, and we'll figure it out together.
        </p>
      </div>
    </div>
  )
}

function ErrorNotice({
  message,
  canRetry,
  onRetry,
  onDismiss,
  retrying,
}: {
  message: string
  canRetry: boolean
  onRetry: () => void
  onDismiss: () => void
  retrying: boolean
}) {
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
      <p className="text-[15px] leading-relaxed text-red-800">{message}</p>
      <div className="mt-2.5 flex gap-2">
        {canRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {retrying ? 'Sending…' : 'Try again'}
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-800 transition-colors hover:bg-red-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}

export function Chat({
  messages,
  sending,
  starting,
  ready,
  error,
  retryable,
  onSend,
  onRetry,
  onDismissError,
}: ChatProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)

  // Record whether the reader is at the bottom *before* the next render adds
  // anything, so the decision below is about where they were, not where the new
  // content just put them.
  const handleScroll = () => {
    const element = scrollRef.current
    if (!element) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    following.current = distance < FOLLOW_THRESHOLD_PX
  }

  useEffect(() => {
    if (!following.current) return
    const element = scrollRef.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  }, [messages, sending, error])

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-slate-50" aria-label="Conversation">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="scroll-quiet min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-5">
          <Welcome />

          {messages.map((message, index) => (
            <MessageBubble
              key={message.key}
              message={message}
              // Only the most recent fallback is worth retrying; older ones
              // have been superseded by whatever was said afterwards.
              onRetry={
                message.fallbackStatus && index === messages.length - 1 ? onRetry : undefined
              }
              retrying={sending}
            />
          ))}

          {sending && <TypingIndicator />}

          {error && (
            <ErrorNotice
              message={error}
              canRetry={retryable !== null}
              onRetry={onRetry}
              onDismiss={onDismissError}
              retrying={sending}
            />
          )}
        </div>
      </div>

      <Composer onSend={onSend} disabled={sending || starting || !ready} sending={sending} />
    </section>
  )
}
