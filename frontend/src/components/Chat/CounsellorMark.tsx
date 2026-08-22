/**
 * The counsellor's avatar: a compass rose.
 *
 * Inline SVG rather than an image file so it inherits colour and needs no
 * network request. A compass because the product's job is orientation, not
 * answers -- and because a robot or a chat bubble would say "chatbot", which is
 * exactly what this is not.
 */
export function CounsellorMark({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white ${className}`}
    >
      <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" strokeWidth="1.8">
        <circle cx="12" cy="12" r="9" stroke="currentColor" />
        <path
          d="M15.2 8.8l-1.9 4.5-4.5 1.9 1.9-4.5 4.5-1.9z"
          stroke="currentColor"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  )
}
