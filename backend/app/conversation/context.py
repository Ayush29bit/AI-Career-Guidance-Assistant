"""Build the deterministic briefing the LLM is allowed to talk about.

Everything in this module is plain data assembly. It reads the engine's result
objects and reshapes them into a compact JSON-serialisable dict; it computes no
score, no gap, no ranking and no ordering of its own. Every number that leaves
here was produced by `app.recommendation`.

That constraint is the whole design. The reply prompt tells the model "the
briefing is the only source of numbers", and this module is what makes that
sentence enforceable: if a figure is not here, the model has never seen it.

Two other things live here because they answer the same question -- what does
the model get to see:

* `profile_summary`, the short prose form of what we already know, so the
  analysis prompt can extract deltas instead of re-extracting everything.
* `scenario_profile`, which applies a hypothetical to a copy of the profile so
  "what if I didn't like statistics" can be scored without touching the stored
  one.

Sizes are capped throughout. The goal is relevant context, not maximum context
(docs/AI.md §7) -- a briefing carrying all eleven careers with every gap would
cost more and explain less.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.conversation.profile_bridge import BridgedProfile
from app.knowledge.loader import KnowledgeBase
from app.llm.schemas import ProfileExtraction
from app.recommendation.courses import CourseRecommendation
from app.recommendation.engine import CareerMatch, RecommendationResult
from app.recommendation.profile import StudentProfile
from app.recommendation.roadmap import Roadmap

#: How much of each list reaches the prompt.
TOP_MATCHES = 3
TOP_STRENGTHS = 5
TOP_CONCERNS = 3
TOP_GAPS = 5
TOP_COURSES = 4


# --------------------------------------------------------------------------
# what we already know
# --------------------------------------------------------------------------

def profile_summary(bridged: BridgedProfile, kb: KnowledgeBase) -> str:
    """One short block describing the stored profile, for the analysis prompt.

    Skill names are included alongside ids because the model reasons about the
    student's words ("I know Python"), and the id alone (`python`) makes the
    already-known check harder than it needs to be.
    """
    profile = bridged.profile
    lines: list[str] = []

    if profile.skills:
        skills = ", ".join(
            f"{kb.skills[skill_id].name} ({skill_id}) {proficiency:.1f}"
            for skill_id, proficiency in sorted(profile.skills.items())
            if skill_id in kb.skills
        )
        lines.append(f"Skills: {skills}")
    if profile.interests:
        lines.append(f"Interests: {', '.join(profile.interests)}")
    if profile.work_preferences:
        lines.append(f"Work preferences: {', '.join(profile.work_preferences)}")
    if profile.experience_level:
        lines.append(f"Experience level: {profile.experience_level}")
    if bridged.strengths:
        lines.append(f"Strengths: {'; '.join(bridged.strengths)}")
    if bridged.dislikes:
        lines.append(f"Dislikes: {'; '.join(bridged.dislikes)}")
    if bridged.goals:
        lines.append(f"Goals: {'; '.join(bridged.goals)}")

    if not lines:
        return "Nothing yet -- this is the start of the conversation."
    return "\n".join(lines)


def profile_facts(bridged: BridgedProfile, kb: KnowledgeBase) -> dict[str, Any]:
    """The stored profile as data, for the briefing."""
    profile = bridged.profile
    return {
        "skills": [
            {
                "skill_id": skill_id,
                "name": kb.skills[skill_id].name if skill_id in kb.skills else skill_id,
                "proficiency": round(proficiency, 2),
            }
            for skill_id, proficiency in sorted(
                profile.skills.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "interests": list(profile.interests),
        "work_preferences": list(profile.work_preferences),
        "experience_level": profile.experience_level,
        "strengths": list(bridged.strengths),
        "dislikes": list(bridged.dislikes),
        "goals": list(bridged.goals),
    }


# --------------------------------------------------------------------------
# hypotheticals
# --------------------------------------------------------------------------

def scenario_profile(profile: StudentProfile, updates: ProfileExtraction) -> StudentProfile:
    """A copy of the profile with a hypothetical applied. Never persisted.

    Removals are applied after additions, so "what if I liked data but not
    statistics" resolves the way the sentence reads.

    No validation call is needed: every value here already survived
    `filter_to_vocabulary`, and the profile it starts from was built by
    `build_profile`, so the result is in-vocabulary by construction.
    """
    skills = dict(profile.skills)
    for skill in updates.skills:
        skills[skill.skill_id] = skill.proficiency
    for skill_id in updates.removed_skills:
        skills.pop(skill_id, None)

    def merged_tags(current: tuple[str, ...], added, removed: list[str]) -> tuple[str, ...]:
        values = list(current)
        for item in added:
            if item.tag not in values:
                values.append(item.tag)
        return tuple(value for value in values if value not in removed)

    return replace(
        profile,
        skills=skills,
        interests=merged_tags(
            profile.interests, updates.interests, updates.removed_interests
        ),
        work_preferences=merged_tags(
            profile.work_preferences,
            updates.work_preferences,
            updates.removed_work_preferences,
        ),
        experience_level=(
            updates.experience.level if updates.experience else profile.experience_level
        ),
    )


def describe_scenario(updates: ProfileExtraction) -> dict[str, Any]:
    """What the hypothetical changed, so the reply can name it accurately."""
    return {
        "added_skills": [s.skill_id for s in updates.skills],
        "added_interests": [t.tag for t in updates.interests],
        "added_work_preferences": [t.tag for t in updates.work_preferences],
        "removed_skills": list(updates.removed_skills),
        "removed_interests": list(updates.removed_interests),
        "removed_work_preferences": list(updates.removed_work_preferences),
        "note": "Hypothetical only. The stored profile was not changed.",
    }


# --------------------------------------------------------------------------
# engine results
# --------------------------------------------------------------------------

def career_facts(match: CareerMatch) -> dict[str, Any]:
    """One career match, trimmed to what an explanation actually needs.

    `score_100` is the engine's own rounded figure -- it is not recomputed here,
    and the reply is told to quote it rather than derive anything from it.
    """
    return {
        "career_id": match.career_id,
        "career_name": match.career_name,
        "description": match.short_description,
        "match_score": match.score_100,
        "score_breakdown": {
            "skill_match": round(match.breakdown.skill_match, 2),
            "interest_match": round(match.breakdown.interest_match, 2),
            "preference_match": round(match.breakdown.preference_match, 2),
            "experience_fit": round(match.breakdown.experience_fit, 2),
            "experience_known": match.breakdown.experience_known,
        },
        "strengths": [factor.label for factor in match.strengths[:TOP_STRENGTHS]],
        "concerns": [factor.label for factor in match.concerns[:TOP_CONCERNS]],
        "matching_interests": list(match.matching_interests),
        "missing_interests": list(match.missing_interests),
        "top_skill_gaps": [
            {
                "skill_id": gap.skill_id,
                "skill_name": gap.skill_name,
                "gap": round(gap.gap, 2),
                "importance": round(gap.importance, 2),
            }
            for gap in match.skill_gaps[:TOP_GAPS]
        ],
        "human_skill_gaps": [gap.skill_name for gap in match.human_skill_gaps[:TOP_GAPS]],
    }


def course_facts(course: CourseRecommendation) -> dict[str, Any]:
    """One course exactly as the catalogue holds it.

    `url` is passed through including its absence: about a third of the
    catalogue has no link, and the reply must be able to say so rather than
    invent one.
    """
    return {
        "course_id": course.course_id,
        "title": course.title,
        "organization": course.organization,
        "url": course.url,
        "has_url": course.has_url,
        "difficulty": course.difficulty,
        "duration": course.duration,
        "rating": course.rating,
        "covers_skills": list(course.covered_gap_skill_names),
        "is_proxy": course.is_proxy,
        "proxy_for": course.proxy_for,
    }


def roadmap_facts(roadmap: Roadmap) -> dict[str, Any]:
    """The learning sequence, in the order the engine put it in.

    `blocked_by` is carried through because it is the reason a lower-priority
    skill sometimes comes first -- without it the ordering looks arbitrary and
    the model is liable to "fix" it.
    """
    return {
        "career_id": roadmap.career_id,
        "career_name": roadmap.career_name,
        "target_level": roadmap.target_level,
        "stages": [
            {
                "stage": stage.stage_number,
                "skill_id": stage.skill_id,
                "skill_name": stage.skill_name,
                "gap": round(stage.gap, 2),
                "priority_rank": stage.priority_rank,
                "blocked_by": list(stage.blocked_by),
                "coverage": stage.coverage,
                "courses": [course_facts(course) for course in stage.courses],
            }
            for stage in roadmap.stages
        ],
        "skipped_gaps": list(roadmap.skipped_gaps),
        "uncovered_skills": list(roadmap.uncovered_skills),
    }


def recommendation_facts(result: RecommendationResult, limit: int = TOP_MATCHES) -> dict[str, Any]:
    """The ranking, or the honest statement that there isn't one yet."""
    if not result.is_ok:
        return {
            "status": result.status,
            "ready": False,
            "missing_information": [
                {"field": item.field_name, "have": item.have, "need": item.need}
                for item in result.missing_information
            ],
        }
    return {
        "status": result.status,
        "ready": True,
        "considered_careers": result.considered_careers,
        "matches": [career_facts(match) for match in result.matches[:limit]],
    }


# --------------------------------------------------------------------------
# the briefing
# --------------------------------------------------------------------------

def build_briefing(
    *,
    intent: str,
    bridged: BridgedProfile,
    kb: KnowledgeBase,
    result: RecommendationResult | None = None,
    focus_careers: tuple[CareerMatch, ...] = (),
    courses: tuple[CourseRecommendation, ...] = (),
    roadmap: Roadmap | None = None,
    scenario: dict[str, Any] | None = None,
    scenario_result: RecommendationResult | None = None,
    notes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Assemble everything the reply is allowed to draw on this turn.

    Sections are omitted rather than sent empty: an absent `courses` key reads
    as "not part of this turn", where `"courses": []` reads as "we looked and
    there are none". Those are different statements and the model should be
    able to make either one truthfully.
    """
    briefing: dict[str, Any] = {
        "intent": intent,
        "known_profile": profile_facts(bridged, kb),
    }

    if result is not None:
        briefing["recommendations"] = recommendation_facts(result)

    if focus_careers:
        briefing["careers_in_focus"] = [career_facts(match) for match in focus_careers]

    if courses:
        briefing["courses"] = [course_facts(course) for course in courses[:TOP_COURSES]]

    if roadmap is not None:
        briefing["roadmap"] = roadmap_facts(roadmap)

    if scenario is not None:
        briefing["scenario"] = scenario
        if scenario_result is not None:
            briefing["scenario_recommendations"] = recommendation_facts(scenario_result)

    if notes:
        briefing["notes"] = list(notes)

    return briefing
