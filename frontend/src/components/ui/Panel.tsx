/**
 * The shared shapes the right-hand rail is built from.
 *
 * Three small primitives rather than three near-identical card implementations.
 * They exist because every section of the panel needs the same header, the same
 * empty state and the same chip -- not as a general-purpose component library.
 */

import type { ReactNode } from 'react'

export function PanelSection({
  title,
  count,
  children,
}: {
  title: string
  /** Shown beside the title when there is something to count. */
  count?: number
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-[11px] font-semibold tracking-[0.08em] text-slate-500 uppercase">
          {title}
        </h3>
        {count !== undefined && count > 0 && (
          <span className="text-xs font-medium text-slate-400">{count}</span>
        )}
      </div>
      {children}
    </section>
  )
}

/** What a section says before the conversation has produced anything for it.
 *
 * Always phrased as "not yet", never as zero. Nothing has been measured and
 * found absent -- the student simply has not mentioned it. */
export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-slate-400">{children}</p>
}

export function Chip({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'brand' | 'positive' | 'caution'
}) {
  const tones = {
    neutral: 'bg-slate-100 text-slate-700',
    brand: 'bg-brand-50 text-brand-700',
    positive: 'bg-emerald-50 text-emerald-700',
    caution: 'bg-amber-50 text-amber-700',
  }
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

/**
 * A 0..1 value as a bar.
 *
 * Presentation of a number the backend computed -- proficiency, gap size, match
 * score. The component never derives the value, only its width.
 */
export function Meter({
  value,
  tone = 'brand',
}: {
  value: number
  tone?: 'brand' | 'amber' | 'slate'
}) {
  const tones = {
    brand: 'bg-brand-600',
    amber: 'bg-amber-500',
    slate: 'bg-slate-400',
  }
  const width = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div
        className={`h-full rounded-full ${tones[tone]} transition-[width] duration-500 ease-out`}
        style={{ width: `${width}%` }}
      />
    </div>
  )
}
