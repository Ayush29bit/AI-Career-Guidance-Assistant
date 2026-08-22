/**
 * What the counsellor currently knows about the student.
 *
 * Every value here came from the last turn's `profile` object. The panel adds
 * no facts: it does not guess an experience level, it does not infer a skill
 * from an interest, and it renders a section empty rather than filling it.
 *
 * The readiness line is the honest version of a progress bar. It comes from the
 * engine's own `missing_information`, so the panel and the engine can never
 * disagree about whether there is enough to rank.
 */

import type { ProfileResponse } from '../../types/api'
import { interestLabel, joinWords, missingFieldLabel, workPreferenceLabel } from '../../lib/labels'
import { Chip, EmptyState, Meter, PanelSection } from '../ui/Panel'

function Skills({ skills }: { skills: ProfileResponse['skills'] }) {
  if (skills.length === 0) {
    return <EmptyState>No skills mentioned yet.</EmptyState>
  }
  return (
    <ul className="flex flex-col gap-2.5">
      {skills.map((skill) => (
        <li key={skill.skill_id}>
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="truncate text-sm font-medium text-slate-700">{skill.name}</span>
            {/* The engine's stored proficiency, shown as a percentage. Rounded
                for display only -- the value itself is the backend's. */}
            <span className="shrink-0 text-xs tabular-nums text-slate-400">
              {Math.round(skill.proficiency * 100)}%
            </span>
          </div>
          <Meter value={skill.proficiency} />
        </li>
      ))}
    </ul>
  )
}

function Tags({
  values,
  label,
  empty,
}: {
  values: string[]
  label: (value: string) => string
  empty: string
}) {
  if (values.length === 0) {
    return <EmptyState>{empty}</EmptyState>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <Chip key={value} tone="brand">
          {label(value)}
        </Chip>
      ))}
    </div>
  )
}

function Notes({ title, values }: { title: string; values: string[] }) {
  if (values.length === 0) return null
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-slate-500">{title}</p>
      <ul className="flex flex-col gap-1">
        {values.map((value) => (
          <li key={value} className="text-sm leading-relaxed text-slate-700">
            {value}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ProfilePanel({ profile }: { profile: ProfileResponse | null }) {
  if (!profile) {
    return (
      <PanelSection title="Your profile">
        <EmptyState>
          This fills in as we talk. Nothing here is a form — I pick it up from what you say.
        </EmptyState>
      </PanelSection>
    )
  }

  const hasNotes =
    profile.strengths.length > 0 || profile.dislikes.length > 0 || profile.goals.length > 0

  return (
    <div className="flex flex-col gap-3">
      <PanelSection title="Your profile" count={profile.signal_count}>
        {profile.signal_count === 0 ? (
          <EmptyState>
            This fills in as we talk. Nothing here is a form — I pick it up from what you say.
          </EmptyState>
        ) : (
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Skills</p>
              <Skills skills={profile.skills} />
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Interests</p>
              <Tags
                values={profile.interests}
                label={interestLabel}
                empty="Not mentioned yet."
              />
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Ways of working</p>
              <Tags
                values={profile.work_preferences}
                label={workPreferenceLabel}
                empty="Not mentioned yet."
              />
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Experience</p>
              {/* Null is a real state the engine treats as neutral, so it is
                  said plainly rather than defaulted to "beginner". */}
              {profile.experience_level ? (
                <Chip>
                  {profile.experience_level.charAt(0).toUpperCase() +
                    profile.experience_level.slice(1)}
                </Chip>
              ) : (
                <EmptyState>Not established yet.</EmptyState>
              )}
            </div>
          </div>
        )}
      </PanelSection>

      {hasNotes && (
        <PanelSection title="In your words">
          <div className="flex flex-col gap-3">
            <Notes title="Strengths" values={profile.strengths} />
            <Notes title="Dislikes" values={profile.dislikes} />
            <Notes title="Goals" values={profile.goals} />
          </div>
        </PanelSection>
      )}

      {!profile.ready_for_recommendations && profile.missing_information.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-slate-100/60 px-4 py-3">
          <p className="text-sm leading-relaxed text-slate-600">
            Still getting to know you — I'd like a clearer picture of your{' '}
            {joinWords(profile.missing_information.map((item) => missingFieldLabel(item.field_name)))}{' '}
            before suggesting careers.
          </p>
        </div>
      )}
    </div>
  )
}
