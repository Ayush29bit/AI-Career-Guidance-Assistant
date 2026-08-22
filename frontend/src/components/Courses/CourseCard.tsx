/**
 * One course from the Coursera catalogue.
 *
 * Not currently reachable: the engine ranks courses and the conversation layer
 * already hands them to the LLM, but no endpoint returns them yet. This is
 * written against the engine's real `CourseRecommendation` shape so it works
 * the day one does -- and it constructs nothing, so there is no placeholder
 * course anywhere in the app.
 *
 * Two details the dataset forces and the UI has to respect:
 *
 * - about a third of the catalogue has no URL, so the card states that rather
 *   than rendering a dead link;
 * - a proxy course teaches an adjacent skill, not the one asked for, and says
 *   so instead of quietly standing in for it.
 */

import type { CourseRecommendation } from '../../types/api'
import { Chip } from '../ui/Panel'

export function CourseCard({ course }: { course: CourseRecommendation }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-3.5">
      <div className="flex items-start justify-between gap-3">
        <h4 className="text-sm leading-snug font-semibold text-slate-900">{course.title}</h4>
        {course.rating !== null && (
          <span className="shrink-0 text-xs tabular-nums text-slate-500">
            ★ {course.rating.toFixed(1)}
          </span>
        )}
      </div>

      <p className="mt-1 text-xs text-slate-500">{course.organization}</p>

      {course.is_proxy && course.proxy_for && (
        <p className="mt-2 text-xs leading-relaxed text-amber-700">
          No course covers {course.proxy_for} directly — this is the closest adjacent topic.
        </p>
      )}

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {course.covered_gap_skill_names.map((skill) => (
          <Chip key={skill} tone="brand">
            {skill}
          </Chip>
        ))}
        {course.difficulty && <Chip>{course.difficulty}</Chip>}
        {course.duration && <Chip>{course.duration}</Chip>}
      </div>

      {course.has_url && course.url ? (
        <a
          href={course.url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700"
        >
          View on Coursera
          <svg viewBox="0 0 24 24" className="size-3.5" fill="none" strokeWidth="2">
            <path
              d="M7 17L17 7M17 7H8M17 7v9"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </a>
      ) : (
        <p className="mt-3 text-xs text-slate-400">No link in the catalogue for this course.</p>
      )}
    </article>
  )
}
