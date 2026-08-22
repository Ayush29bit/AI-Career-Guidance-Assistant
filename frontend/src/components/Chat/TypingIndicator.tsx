/**
 * The counsellor is thinking.
 *
 * A real wait, not a decorative one: a turn makes two provider calls and takes
 * several seconds, so the state has to be unmistakable. Three dots in the same
 * card shape the reply will arrive in, so nothing jumps when it does.
 */
import { CounsellorMark } from './CounsellorMark'

export function TypingIndicator() {
  return (
    <div className="flex items-start gap-3" aria-live="polite" aria-label="Counsellor is typing">
      <CounsellorMark />
      <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex items-center gap-1.5">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="size-2 animate-bounce rounded-full bg-slate-300"
              style={{ animationDelay: `${index * 150}ms`, animationDuration: '1s' }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
