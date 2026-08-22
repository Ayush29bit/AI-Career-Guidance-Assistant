/**
 * The backend's response shapes, mirrored.
 *
 * These are a hand-written copy of the FastAPI response models
 * (`backend/app/api/`). Everything here is data the server computed: match
 * scores, skill gaps, priorities, course rankings. Nothing in the frontend
 * derives any of it -- if a number is on screen, it arrived in one of these
 * fields.
 *
 * The shapes are kept in the same order as the Python models so a change on
 * either side is easy to line up against the other.
 */

// --------------------------------------------------------------------------
// conversation
// --------------------------------------------------------------------------

export interface MessageOut {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
}

/** The counsellor's turn -- or the fallback that stood in for it.
 *
 * `persisted` is false exactly when the LLM could not be reached. The text is
 * still worth showing, but it is not a counsellor turn and the UI must not
 * present it as one. */
export interface AssistantReply {
  role: 'assistant'
  content: string
  persisted: boolean
  id: number | null
  created_at: string | null
}

export interface ConversationCreated {
  conversation_id: string
  profile_id: string
  created_at: string
}

export interface ConversationDetail {
  conversation_id: string
  profile_id: string | null
  created_at: string
  updated_at: string
  message_count: number
  messages: MessageOut[]
}

/** Why a turn ended the way it did. */
export type TurnStatus = 'ok' | 'llm_unavailable' | 'llm_error'

export interface TurnResponse {
  conversation_id: string
  user_message: MessageOut
  message: AssistantReply
  intent: string
  status: TurnStatus
  profile: ProfileResponse
  /** Null when the profile is too thin to rank, or the turn failed early. */
  recommendations: RecommendationsResponse | null
}

// --------------------------------------------------------------------------
// profile
// --------------------------------------------------------------------------

export interface ProfileSkill {
  skill_id: string
  name: string
  category: string
  kind: string
  proficiency: number
}

export interface MissingInformation {
  field_name: string
  have: number
  need: number
}

export interface ProfileResponse {
  profile_id: string
  /** Null means unknown, which the engine treats as neutral. Never "beginner". */
  experience_level: string | null
  skills: ProfileSkill[]
  interests: string[]
  work_preferences: string[]
  strengths: string[]
  dislikes: string[]
  goals: string[]
  signal_count: number
  ready_for_recommendations: boolean
  missing_information: MissingInformation[]
}

// --------------------------------------------------------------------------
// recommendations
// --------------------------------------------------------------------------

export interface MatchFactor {
  kind: 'skill' | 'interest' | 'work_preference' | 'experience'
  id: string
  label: string
  value: number
  weight: number
}

export interface SkillGap {
  skill_id: string
  skill_name: string
  required_level: number
  student_level: number
  gap: number
  importance: number
  /** gap x importance -- what orders the list. Computed by the engine. */
  priority: number
  is_human_skill: boolean
}

export interface ScoreBreakdown {
  skill_match: number
  interest_match: number
  preference_match: number
  experience_fit: number
  skill_contribution: number
  interest_contribution: number
  preference_contribution: number
  experience_contribution: number
  /** False when experience is unknown, so the UI shows that term as neutral. */
  experience_known: boolean
}

export interface CareerMatch {
  career_id: string
  career_name: string
  short_description: string
  score: number
  /** The engine's own rounded figure. Displayed as-is, never recomputed. */
  score_100: number
  breakdown: ScoreBreakdown
  strengths: MatchFactor[]
  concerns: MatchFactor[]
  matching_interests: string[]
  missing_interests: string[]
  matching_preferences: string[]
  missing_preferences: string[]
  skill_gaps: SkillGap[]
  human_skill_gaps: SkillGap[]
}

export interface RecommendationsResponse {
  profile_id: string
  status: 'ok' | 'insufficient_profile'
  considered_careers: number
  matches: CareerMatch[]
  missing_information: MissingInformation[]
}

// --------------------------------------------------------------------------
// courses and roadmap
// --------------------------------------------------------------------------

/**
 * Courses and roadmaps exist in the engine (`app/recommendation/courses.py`,
 * `roadmap.py`) and the conversation layer already hands them to the LLM, but
 * no endpoint returns them yet. These types mirror the engine's dataclasses so
 * the components built against them are ready the day an endpoint does.
 *
 * Nothing in the app constructs one of these. There is no placeholder course.
 */
export interface CourseRecommendation {
  course_id: string
  title: string
  organization: string
  url: string | null
  has_url: boolean
  score: number
  score_100: number
  covered_gap_skills: string[]
  covered_gap_skill_names: string[]
  is_proxy: boolean
  proxy_for: string | null
  difficulty: string | null
  course_type: string | null
  duration: string | null
  rating: number | null
  review_count: number | null
  review_count_is_approximate: boolean
}

export interface RoadmapStage {
  stage_number: number
  skill_id: string
  skill_name: string
  gap: number
  required_level: number
  student_level: number
  priority: number
  priority_rank: number
  /** Gap skills that had to come first -- why a stage is not in priority order. */
  blocked_by: string[]
  coverage: 'good' | 'thin' | 'proxy' | 'none'
  courses: CourseRecommendation[]
  proxy_skills: string[]
}

export interface Roadmap {
  career_id: string
  career_name: string
  target_level: string | null
  stages: RoadmapStage[]
  skipped_gaps: string[]
  uncovered_skills: string[]
}
