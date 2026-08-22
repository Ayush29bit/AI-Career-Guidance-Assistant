/**
 * Display labels for the backend's controlled vocabularies.
 *
 * This is presentation only. Every id here comes from the backend and is shown
 * because the backend sent it -- the map decides how `ai_ml` is spelled on
 * screen, never whether the student has it. No entry adds information the API
 * did not return.
 *
 * Unknown ids fall back to title-casing rather than being dropped, so a tag
 * added to careers.yaml still renders sensibly before anyone updates this file.
 * That is the whole reason for the fallback: a stale map should degrade, not
 * hide a fact about the student.
 */

const INTEREST_LABELS: Record<string, string> = {
  ai_ml: 'AI & Machine Learning',
  data: 'Data',
  software: 'Software',
  web: 'Web',
  cloud: 'Cloud',
  security: 'Security',
  product: 'Product',
  design: 'Design',
  business: 'Business',
  research: 'Research',
}

const WORK_LABELS: Record<string, string> = {
  building: 'Building',
  analysis: 'Analysis',
  experimentation: 'Experimentation',
  systems_thinking: 'Systems thinking',
  problem_solving: 'Problem solving',
  creativity: 'Creativity',
  people_facing: 'People-facing',
  structured_process: 'Structured process',
  visual: 'Visual work',
  detail_oriented: 'Detail-oriented',
}

/** What the engine still needs before it will rank anything. */
const MISSING_FIELD_LABELS: Record<string, string> = {
  skills: 'skills',
  interests: 'interests',
  work_preferences: 'work preferences',
}

/** `deep_learning` -> `Deep learning`. The last-resort readable form. */
export function titleCase(id: string): string {
  const spaced = id.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function interestLabel(tag: string): string {
  return INTEREST_LABELS[tag] ?? titleCase(tag)
}

export function workPreferenceLabel(tag: string): string {
  return WORK_LABELS[tag] ?? titleCase(tag)
}

export function missingFieldLabel(field: string): string {
  return MISSING_FIELD_LABELS[field] ?? titleCase(field).toLowerCase()
}

/**
 * A match factor's display label.
 *
 * Skills already arrive with a human name from the knowledge base; tags arrive
 * as ids and need the maps above. Experience factors carry the level itself.
 */
export function factorLabel(kind: string, label: string): string {
  if (kind === 'interest') return interestLabel(label)
  if (kind === 'work_preference') return workPreferenceLabel(label)
  if (kind === 'experience') return `${titleCase(label)} level`
  return label
}

/** Join a list the way a sentence would. Used in the cold-start prompt. */
export function joinWords(words: string[], conjunction = 'and'): string {
  if (words.length === 0) return ''
  if (words.length === 1) return words[0]
  return `${words.slice(0, -1).join(', ')} ${conjunction} ${words[words.length - 1]}`
}

/** Clock time for a message, e.g. "14:32". Locale-aware, no seconds. */
export function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
