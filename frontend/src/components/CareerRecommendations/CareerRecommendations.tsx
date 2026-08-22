/**
 * The ranking, or an honest statement that there isn't one yet.
 *
 * `insufficient_profile` is a real answer from the engine, not an error and not
 * an empty list -- so it gets its own state rather than rendering as "no
 * results". Saying "I don't know enough yet" is the product working correctly.
 */

import type { RecommendationsResponse } from '../../types/api'
import { joinWords, missingFieldLabel } from '../../lib/labels'
import { EmptyState, PanelSection } from '../ui/Panel'
import { CareerCard } from './CareerCard'

interface CareerRecommendationsProps {
  recommendations: RecommendationsResponse | null
  /** True while a turn is in flight, so a stale ranking reads as stale. */
  refreshing: boolean
}

export function CareerRecommendations({
  recommendations,
  refreshing,
}: CareerRecommendationsProps) {
  if (!recommendations || recommendations.status !== 'ok') {
    const missing = recommendations?.missing_information ?? []
    return (
      <PanelSection title="Career matches">
        <EmptyState>
          {missing.length > 0 ? (
            <>
              I need a clearer sense of your{' '}
              {joinWords(missing.map((item) => missingFieldLabel(item.field_name)))} before I can
              rank anything honestly.
            </>
          ) : (
            <>Matches appear once I know enough about you to rank them.</>
          )}
        </EmptyState>
      </PanelSection>
    )
  }

  return (
    <PanelSection title="Career matches" count={recommendations.matches.length}>
      <div
        className={`flex flex-col gap-2.5 transition-opacity ${refreshing ? 'opacity-50' : ''}`}
      >
        {recommendations.matches.map((match, index) => (
          <CareerCard key={match.career_id} match={match} rank={index + 1} />
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-400">
        Scored against {recommendations.considered_careers} careers.
      </p>
    </PanelSection>
  )
}
