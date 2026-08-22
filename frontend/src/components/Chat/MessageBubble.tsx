/**
 * One turn in the conversation.
 *
 * The two roles are shaped differently on purpose. The student's words sit in a
 * solid blue bubble on the right -- brief, and clearly theirs. The counsellor's
 * reply is a white card on the left with room to breathe, because it is the
 * thing being read.
 *
 * A third shape exists for a reply the server did not persist: the LLM was
 * unreachable and this text stood in for a counsellor turn. It is rendered as a
 * notice, not as advice, because presenting it as the counsellor speaking would
 * be a small lie about what happened.
 */

import type { ChatMessage } from '../../hooks/useConversation'
import { formatTime } from '../../lib/labels'
import { CounsellorMark } from './CounsellorMark'

interface MessageBubbleProps {
  message: ChatMessage
  /** Retry the message that produced this fallback. */
  onRetry?: () => void
  retrying?: boolean
}

export function MessageBubble({ message, onRetry, retrying }: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <div className="animate-message-in flex justify-end">
        <div className="max-w-[85%] sm:max-w-[75%]">
          {/* break-words is what keeps a pasted URL or an unbroken string from
              stretching the whole column. */}
          <div className="rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] leading-relaxed break-words whitespace-pre-wrap text-white">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  if (message.fallbackStatus) {
    return (
      <div className="animate-message-in flex justify-start">
        <div className="max-w-[92%] rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 sm:max-w-[80%]">
          <p className="text-[13px] font-semibold tracking-wide text-amber-900 uppercase">
            {message.fallbackStatus === 'llm_unavailable'
              ? 'Counsellor unavailable'
              : 'Reply not delivered'}
          </p>
          <p className="mt-1 text-[15px] leading-relaxed break-words whitespace-pre-wrap text-amber-900">
            {message.content}
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="mt-2.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {retrying ? 'Sending…' : 'Try again'}
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="animate-message-in flex items-start gap-3">
      <CounsellorMark />
      <div className="min-w-0 max-w-[92%] sm:max-w-[80%]">
        <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <p className="text-[15px] leading-relaxed break-words whitespace-pre-wrap text-slate-800">
            {message.content}
          </p>
        </div>
        {/* Timestamps only on the counsellor's turns: enough to anchor the
            conversation in time without stamping every line. */}
        <p className="mt-1 pl-1 text-xs text-slate-400">{formatTime(message.createdAt)}</p>
      </div>
    </div>
  )
}
