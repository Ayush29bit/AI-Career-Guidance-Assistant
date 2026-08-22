/**
 * One ranked career.
 *
 * The score is `score_100`, printed exactly as the engine produced it. The
 * frontend has the full breakdown available and still does not add, average or
 * rescale anything -- if the number on screen is wrong, there is one place to
 * look, and it is not here.
 *
 * Strengths and concerns are the engine's own `MatchFactor` lists, already
 * ordered by weight. The card shows the top few rather than all of them: the
 * panel supports the conversation, and the conversation is where the full
 * reasoning gets explained.
 */

import { useState } from 'react'
import type { CareerMatch } from '../../types/api'
import { factorLabel } from '../../lib/labels'
import { Chip, Meter } from '../ui/Panel'

const VISIBLE_FACTORS = 3

export function CareerCard({ match, rank }: { match: CareerMatch; rank: number }) {
  // The top match opens by default: it is the one the counsellor will have just
  // been talking about.
  const [expanded, setExpanded] = useState(rank === 1)

  return (
    <article className="rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
        className="w-full rounded-xl px-4 py-3.5 text-left transition-colors hover:bg-slate-50"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h4 className="truncate text-[15px] font-semibold text-slate-900">
              {match.career_name}
            </h4>
            <p className="mt-0.5 text-xs text-slate-500">
              {rank === 1 ? 'Strongest match so far' : `#${rank} match`}
            </p>
          </div>
          <div className="shrink-0 text-right">
            <span className="text-lg font-semibold tabular-nums text-brand-600">
              {match.score_100}
              <span className="text-sm font-medium text-slate-400">%</span>
            </span>
          </div>
        </div>
        <div className="mt-2.5">
          {/* `score` is the engine's own 0..1 value -- the same number
              `score_100` was rounded from, not a second calculation. */}
          <Meter value={match.score} />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-100 px-4 py-3.5">
          <p className="text-sm leading-relaxed text-slate-600">{match.short_description}</p>

          {match.strengths.length > 0 && (
            <div className="mt-3.5">
              <p className="mb-1.5 text-xs font-medium text-slate-500">What fits</p>
              <div className="flex flex-wrap gap-1.5">
                {match.strengths.slice(0, VISIBLE_FACTORS).map((factor) => (
                  <Chip key={`${factor.kind}-${factor.id}`} tone="positive">
                    {factorLabel(factor.kind, factor.label)}
                  </Chip>
                ))}
              </div>
            </div>
          )}

          {match.concerns.length > 0 && (
            <div className="mt-3">
              <p className="mb-1.5 text-xs font-medium text-slate-500">Worth thinking about</p>
              <div className="flex flex-wrap gap-1.5">
                {match.concerns.slice(0, VISIBLE_FACTORS).map((factor) => (
                  <Chip key={`${factor.kind}-${factor.id}`} tone="caution">
                    {factorLabel(factor.kind, factor.label)}
                  </Chip>
                ))}
              </div>
            </div>
          )}

          <p className="mt-3.5 text-xs leading-relaxed text-slate-400">
            Ask why this fits, or how it compares — I'll explain it properly.
          </p>
        </div>
      )}
    </article>
  )
}
