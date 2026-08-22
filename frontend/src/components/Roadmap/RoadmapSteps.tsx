/**
 * The learning sequence towards one career.
 *
 * Not currently reachable, for the same reason as CourseCard: the engine builds
 * roadmaps but no endpoint returns one yet. Written against the real `Roadmap`
 * shape so it is ready when one does.
 *
 * `blocked_by` is rendered rather than hidden. It is the reason a lower-priority
 * skill sometimes comes first, and without it the ordering looks arbitrary --
 * which is exactly the kind of unexplained result this product exists to avoid.
 */

import type { Roadmap } from '../../types/api'
import { CourseCard } from '../Courses/CourseCard'
import { EmptyState, PanelSection } from '../ui/Panel'

export function RoadmapSteps({ roadmap }: { roadmap: Roadmap | null }) {
  if (!roadmap || roadmap.stages.length === 0) {
    return (
      <PanelSection title="Learning path">
        <EmptyState>Ask what to learn first and I'll sequence it.</EmptyState>
      </PanelSection>
    )
  }

  return (
    <PanelSection title="Learning path" count={roadmap.stages.length}>
      <p className="mb-3 text-xs text-slate-500">
        Towards <span className="font-medium text-slate-700">{roadmap.career_name}</span>
      </p>

      <ol className="flex flex-col gap-4">
        {roadmap.stages.map((stage) => (
          <li key={stage.skill_id} className="relative pl-7">
            <span className="absolute top-0 left-0 flex size-5 items-center justify-center rounded-full bg-brand-50 text-[11px] font-semibold text-brand-700">
              {stage.stage_number}
            </span>

            <p className="text-sm font-medium text-slate-800">{stage.skill_name}</p>

            {stage.blocked_by.length > 0 && (
              <p className="mt-0.5 text-xs text-slate-500">
                Comes after {stage.blocked_by.join(', ')}
              </p>
            )}

            {stage.coverage === 'none' && (
              <p className="mt-1 text-xs text-slate-400">
                No course in the catalogue covers this yet.
              </p>
            )}

            {stage.courses.length > 0 && (
              <div className="mt-2 flex flex-col gap-2">
                {stage.courses.map((course) => (
                  <CourseCard key={course.course_id} course={course} />
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>

      {roadmap.skipped_gaps.length > 0 && (
        <p className="mt-3 text-xs text-slate-400">
          {roadmap.skipped_gaps.length} further gaps beyond this sequence.
        </p>
      )}
    </PanelSection>
  )
}
