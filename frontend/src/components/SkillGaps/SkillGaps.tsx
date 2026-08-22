/**
 * The gaps standing between the student and their strongest match.
 *
 * `skill_gaps` arrives already ordered by the engine's priority (gap x
 * importance) and already filtered to technical skills -- human-skill gaps are
 * kept in a separate field precisely so they never drive course
 * recommendations. This component preserves that order and shows only the top
 * few: the whole list belongs in a roadmap, not in a sidebar.
 *
 * The bar is the gap itself. The priority label is the engine's number, banded
 * for display only.
 */

import type { CareerMatch } from '../../types/api'
import { EmptyState, Meter, PanelSection } from '../ui/Panel'

const VISIBLE_GAPS = 4

/** Bands for the engine's `priority`. Presentation of a computed value -- the
 * ordering is the backend's, and this only decides what to call each band. */
function priorityLabel(priority: number): { text: string; tone: 'amber' | 'slate' } {
  if (priority >= 0.4) return { text: 'High priority', tone: 'amber' }
  if (priority >= 0.2) return { text: 'Medium priority', tone: 'amber' }
  return { text: 'Lower priority', tone: 'slate' }
}

export function SkillGaps({ match }: { match: CareerMatch | null }) {
  if (!match) {
    return (
      <PanelSection title="Skill gaps">
        <EmptyState>Gaps appear once there's a career to measure against.</EmptyState>
      </PanelSection>
    )
  }

  const gaps = match.skill_gaps.slice(0, VISIBLE_GAPS)
  const remaining = match.skill_gaps.length - gaps.length

  if (gaps.length === 0) {
    return (
      <PanelSection title="Skill gaps">
        <EmptyState>
          Nothing outstanding for {match.career_name} based on what I know so far.
        </EmptyState>
      </PanelSection>
    )
  }

  return (
    <PanelSection title="Top gaps" count={match.skill_gaps.length}>
      <p className="mb-3 text-xs text-slate-500">
        To move towards <span className="font-medium text-slate-700">{match.career_name}</span>
      </p>
      <ul className="flex flex-col gap-3">
        {gaps.map((gap) => {
          const priority = priorityLabel(gap.priority)
          return (
            <li key={gap.skill_id}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <span className="truncate text-sm font-medium text-slate-700">
                  {gap.skill_name}
                </span>
                <span
                  className={`shrink-0 text-[11px] font-medium ${
                    priority.tone === 'amber' ? 'text-amber-600' : 'text-slate-400'
                  }`}
                >
                  {priority.text}
                </span>
              </div>
              <Meter value={gap.gap} tone={priority.tone} />
            </li>
          )
        })}
      </ul>
      {remaining > 0 && (
        <p className="mt-3 text-xs text-slate-400">
          {remaining} more — ask what to learn first and I'll sequence them.
        </p>
      )}
    </PanelSection>
  )
}
