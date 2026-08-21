"""The conversational turn, end to end, against a fake LLM.

These need a reachable PostgreSQL with the migrations applied, so the whole
module skips cleanly when there is not one -- as in test_api.py, the rest of the
suite must stay runnable on a machine with no database.

Isolation is the same transaction-never-committed trick used there, so a turn's
`commit()` releases a savepoint and the rollback in tearDown leaves the database
exactly as it was found.

The fake LLM is the point. Every test here scripts what the model returns and
then asserts what the *application* did with it: what reached the profile, what
reached the briefing, and -- more importantly -- what did not. A real provider
would make all of that non-deterministic and none of it any more realistic,
because the code path under test is identical either way.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

_SKIP_REASON: str | None = None
_app = None

try:  # importing app.main needs DATABASE_URL, which may not be configured
    from app.database.session import get_engine, get_session
    from app.main import app as _app

    with get_engine().connect() as _connection:
        _connection.execute(text("SELECT 1"))
except Exception as error:  # pragma: no cover - environment dependent
    _SKIP_REASON = f"PostgreSQL is not reachable: {error}"

if _SKIP_REASON is None:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api.deps import get_knowledge_base, get_llm_client
    from app.conversation.profile_bridge import to_student_profile
    from app.conversation.service import handle_user_message
    from app.database import models
    from app.recommendation.courses import load_catalogue
    from app.recommendation.engine import recommend_careers

from tests.fakes import FakeLLM, analysis, briefing_in, empty_analysis

API = "/api/v1"

#: A profile rich enough to clear the cold-start floor and produce a ranking.
RICH_SKILLS = {"python": 0.8, "machine_learning": 0.7, "sql": 0.6, "statistics": 0.4}
RICH_INTERESTS = ("ai_ml", "data")
RICH_PREFERENCES = ("building", "experimentation")

#: Built once: the ingestion parses a 600-row CSV, and it is the same catalogue
#: for every test.
_CATALOGUE = None


def catalogue():
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = load_catalogue(get_knowledge_base())
    return _CATALOGUE


@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class ConversationTestCase(unittest.TestCase):
    def setUp(self):
        self.kb = get_knowledge_base()
        self.connection = get_engine().connect()
        self.transaction = self.connection.begin()
        self.session = Session(
            bind=self.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        _app.dependency_overrides[get_session] = lambda: self.session
        # Default to no LLM so a test that forgets `use_llm` falls back rather
        # than calling a real provider. Nothing here may touch the network.
        _app.dependency_overrides[get_llm_client] = lambda: None
        self.client = TestClient(_app)

    def tearDown(self):
        _app.dependency_overrides.clear()
        self.session.close()
        self.transaction.rollback()
        self.connection.close()

    # -- helpers ----------------------------------------------------------

    def use_llm(self, llm) -> None:
        """Point the API at a scripted client for the rest of this test."""
        _app.dependency_overrides[get_llm_client] = lambda: llm

    def start_conversation(self) -> tuple[str, str]:
        response = self.client.post(f"{API}/conversations")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        return body["conversation_id"], body["profile_id"]

    def send(self, conversation_id: str, content: str):
        response = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": content}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def history(self, conversation_id: str) -> list[dict]:
        return self.client.get(f"{API}/conversations/{conversation_id}").json()["messages"]

    def db_profile(self, profile_id: str) -> models.StudentProfile:
        profile = self.session.get(models.StudentProfile, uuid.UUID(profile_id))
        self.session.refresh(profile)
        return profile

    def populate(
        self,
        profile_id: str,
        *,
        skills: dict[str, float] | None = None,
        interests: tuple[str, ...] = (),
        work_preferences: tuple[str, ...] = (),
        experience_level: str | None = None,
        source: str = "explicit",
    ) -> None:
        profile = self.session.get(models.StudentProfile, uuid.UUID(profile_id))
        profile.experience_level = experience_level
        if experience_level:
            profile.experience_level_source = source
        for skill_id, proficiency in (skills or {}).items():
            self.session.add(
                models.StudentProfileSkill(
                    student_profile_id=profile.id,
                    skill_id=skill_id,
                    proficiency=proficiency,
                    source=source,
                )
            )
        for tag_type, tags in (("interest", interests), ("work_preference", work_preferences)):
            for tag in tags:
                self.session.add(
                    models.StudentProfileTag(
                        student_profile_id=profile.id,
                        tag_type=tag_type,
                        tag=tag,
                        source=source,
                    )
                )
        self.session.flush()
        self.session.expire(profile)

    def rich_conversation(self) -> tuple[str, str]:
        conversation_id, profile_id = self.start_conversation()
        self.populate(
            profile_id,
            skills=dict(RICH_SKILLS),
            interests=RICH_INTERESTS,
            work_preferences=RICH_PREFERENCES,
            experience_level="intermediate",
        )
        return conversation_id, profile_id

    def run_turn(self, conversation_id: str, profile_id: str, content: str, llm):
        """Call the service directly, for turns that need the course catalogue."""
        conversation = self.session.get(models.Conversation, uuid.UUID(conversation_id))
        return handle_user_message(
            session=self.session,
            conversation=conversation,
            db_profile=self.db_profile(profile_id),
            content=content,
            kb=self.kb,
            llm=llm,
            catalogue_provider=catalogue,
        )


# --------------------------------------------------------------------------
# cold start
# --------------------------------------------------------------------------

class TestColdStart(ConversationTestCase):
    def test_a_first_message_extracts_and_asks_a_question(self):
        llm = FakeLLM(
            analyses=[
                analysis(
                    "discovery",
                    skills=[("python", 0.6, "explicit")],
                    interests=[("ai_ml", "explicit")],
                )
            ],
            replies=["That's a useful start. Do you prefer building or analysing?"],
        )
        self.use_llm(llm)
        conversation_id, profile_id = self.start_conversation()

        body = self.send(conversation_id, "I like Python and AI but I'm not sure what to choose.")

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["intent"], "discovery")
        self.assertEqual(
            body["message"]["content"],
            "That's a useful start. Do you prefer building or analysing?",
        )
        self.assertEqual([s["skill_id"] for s in body["profile"]["skills"]], ["python"])
        self.assertEqual(body["profile"]["interests"], ["ai_ml"])

    def test_a_thin_profile_produces_no_ranking(self):
        self.use_llm(FakeLLM(analyses=[analysis(skills=[("python", 0.6, "explicit")])]))
        conversation_id, _ = self.start_conversation()

        body = self.send(conversation_id, "I know some Python.")

        self.assertFalse(body["profile"]["ready_for_recommendations"])
        self.assertEqual(body["recommendations"]["status"], "insufficient_profile")
        self.assertEqual(body["recommendations"]["matches"], [])

    def test_the_briefing_tells_the_model_it_cannot_recommend_yet(self):
        llm = FakeLLM(analyses=[analysis(skills=[("python", 0.6, "explicit")])])
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "I know some Python.")

        briefing = llm.last_briefing
        self.assertFalse(briefing["recommendations"]["ready"])
        missing = {item["field"] for item in briefing["recommendations"]["missing_information"]}
        self.assertEqual(missing, {"skills", "interests", "work_preferences"})
        self.assertNotIn("matches", briefing["recommendations"])

    def test_the_analysis_prompt_starts_from_an_empty_profile(self):
        llm = FakeLLM()
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "hello")

        self.assertIn("Nothing yet", llm.json_systems[0])


# --------------------------------------------------------------------------
# profile persistence
# --------------------------------------------------------------------------

class TestProfilePersistence(ConversationTestCase):
    def test_extracted_facts_reach_the_database_with_their_source(self):
        self.use_llm(
            FakeLLM(
                analyses=[
                    analysis(
                        skills=[("python", 0.8, "explicit"), ("sql", 0.4, "inferred")],
                        interests=[("data", "inferred")],
                        experience=("intermediate", "explicit"),
                        goals=[("work on AI products", "explicit")],
                    )
                ]
            )
        )
        conversation_id, profile_id = self.start_conversation()
        self.send(conversation_id, "I've used Python for two years and touched some SQL.")

        profile = self.db_profile(profile_id)
        sources = {row.skill_id: row.source for row in profile.skills}
        self.assertEqual(sources, {"python": "explicit", "sql": "inferred"})
        self.assertEqual(profile.tags[0].source, "inferred")
        self.assertEqual(profile.experience_level_source, "explicit")
        self.assertEqual(profile.entries[0].content, "work on AI products")

    def test_an_inference_does_not_overwrite_a_stated_fact_across_turns(self):
        self.use_llm(
            FakeLLM(
                analyses=[
                    analysis(skills=[("python", 0.9, "explicit")]),
                    analysis(skills=[("python", 0.2, "inferred")]),
                ]
            )
        )
        conversation_id, profile_id = self.start_conversation()
        self.send(conversation_id, "I've been writing Python professionally for years.")
        self.send(conversation_id, "I built a small script once.")

        rows = {row.skill_id: row for row in self.db_profile(profile_id).skills}
        self.assertEqual(rows["python"].proficiency, 0.9)
        self.assertEqual(rows["python"].source, "explicit")

    def test_the_second_analysis_prompt_carries_what_is_already_known(self):
        llm = FakeLLM(
            analyses=[analysis(skills=[("python", 0.8, "explicit")]), empty_analysis()]
        )
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "I know Python.")
        self.send(conversation_id, "What next?")

        self.assertIn("Python (python) 0.8", llm.json_systems[1])

    def test_history_is_replayed_to_the_model(self):
        llm = FakeLLM(replies=["ok"])
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "first")
        self.send(conversation_id, "second")

        roles = [m["role"] for m in llm.histories[-1]]
        contents = [m["content"] for m in llm.histories[-1]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(contents, ["first", "ok", "second"])


# --------------------------------------------------------------------------
# recommendations
# --------------------------------------------------------------------------

class TestRecommendationReadyConversation(ConversationTestCase):
    def test_a_ready_profile_gets_the_engine_ranking(self):
        self.use_llm(FakeLLM(analyses=[empty_analysis("career_recommendation")]))
        conversation_id, profile_id = self.rich_conversation()

        body = self.send(conversation_id, "So what careers suit me?")

        self.assertEqual(body["recommendations"]["status"], "ok")
        self.assertTrue(body["profile"]["ready_for_recommendations"])
        self.assertTrue(body["recommendations"]["matches"])

    def test_the_response_ranking_is_exactly_the_engine_ranking(self):
        """Same profile, same engine, same numbers -- computed independently here."""
        self.use_llm(FakeLLM(analyses=[empty_analysis("career_recommendation")]))
        conversation_id, profile_id = self.rich_conversation()

        body = self.send(conversation_id, "What should I do?")

        expected = recommend_careers(
            to_student_profile(self.db_profile(profile_id), self.kb), self.kb, limit=3
        )
        self.assertEqual(
            [m["career_id"] for m in body["recommendations"]["matches"]],
            [m.career_id for m in expected.matches],
        )
        self.assertEqual(
            [m["score_100"] for m in body["recommendations"]["matches"]],
            [m.score_100 for m in expected.matches],
        )

    def test_the_briefing_carries_the_engine_scores_and_gaps(self):
        llm = FakeLLM(analyses=[empty_analysis("career_recommendation")])
        self.use_llm(llm)
        conversation_id, profile_id = self.rich_conversation()
        self.send(conversation_id, "What should I do?")

        expected = recommend_careers(
            to_student_profile(self.db_profile(profile_id), self.kb), self.kb, limit=3
        )
        matches = llm.last_briefing["recommendations"]["matches"]
        self.assertEqual(
            [m["match_score"] for m in matches], [m.score_100 for m in expected.matches]
        )
        top = matches[0]
        self.assertEqual(
            [gap["skill_id"] for gap in top["top_skill_gaps"]],
            [gap.skill_id for gap in expected.matches[0].skill_gaps[:5]],
        )

    def test_a_named_career_is_put_in_focus(self):
        llm = FakeLLM(
            analyses=[empty_analysis("why_recommendation") | {"careers_mentioned": ["ml_engineer"]}]
        )
        self.use_llm(llm)
        conversation_id, _ = self.rich_conversation()
        self.send(conversation_id, "Why ML Engineering?")

        focus = llm.last_briefing["careers_in_focus"]
        self.assertEqual([c["career_id"] for c in focus], ["ml_engineer"])
        self.assertIn("strengths", focus[0])

    def test_comparison_puts_both_careers_in_focus(self):
        llm = FakeLLM(
            analyses=[
                empty_analysis("career_comparison")
                | {"careers_mentioned": ["ml_engineer", "data_scientist"]}
            ]
        )
        self.use_llm(llm)
        conversation_id, _ = self.rich_conversation()
        self.send(conversation_id, "Compare ML Engineering and Data Science.")

        focus = llm.last_briefing["careers_in_focus"]
        self.assertEqual(
            [c["career_id"] for c in focus], ["ml_engineer", "data_scientist"]
        )


# --------------------------------------------------------------------------
# courses and roadmaps
# --------------------------------------------------------------------------

class TestCourseAndRoadmapTurns(ConversationTestCase):
    def test_courses_come_from_the_catalogue(self):
        llm = FakeLLM(
            analyses=[
                empty_analysis("course_recommendation") | {"careers_mentioned": ["ml_engineer"]}
            ]
        )
        conversation_id, profile_id = self.rich_conversation()
        turn = self.run_turn(conversation_id, profile_id, "What should I study?", llm)

        courses = turn.briefing["courses"]
        self.assertTrue(courses)
        known_ids = {course.course_id for course in catalogue().courses}
        for course in courses:
            self.assertIn(course["course_id"], known_ids)

    def test_a_roadmap_keeps_the_engine_ordering(self):
        llm = FakeLLM(
            analyses=[empty_analysis("roadmap") | {"careers_mentioned": ["ml_engineer"]}]
        )
        conversation_id, profile_id = self.rich_conversation()
        turn = self.run_turn(conversation_id, profile_id, "What should I learn first?", llm)

        stages = turn.briefing["roadmap"]["stages"]
        self.assertTrue(stages)
        self.assertEqual([s["stage"] for s in stages], list(range(1, len(stages) + 1)))

    def test_an_ordinary_turn_never_loads_the_catalogue(self):
        """The expensive path stays off unless the turn is actually about courses."""
        calls = []

        def exploding_catalogue():
            calls.append(1)
            raise AssertionError("the catalogue must not be loaded for this turn")

        conversation_id, profile_id = self.rich_conversation()
        conversation = self.session.get(models.Conversation, uuid.UUID(conversation_id))
        turn = handle_user_message(
            session=self.session,
            conversation=conversation,
            db_profile=self.db_profile(profile_id),
            content="What careers suit me?",
            kb=self.kb,
            llm=FakeLLM(analyses=[empty_analysis("career_recommendation")]),
            catalogue_provider=exploding_catalogue,
        )
        self.assertEqual(calls, [])
        self.assertEqual(turn.status, "ok")


# --------------------------------------------------------------------------
# what-if
# --------------------------------------------------------------------------

class TestWhatIf(ConversationTestCase):
    def test_a_hypothetical_is_not_written_to_the_profile(self):
        self.use_llm(
            FakeLLM(
                analyses=[
                    analysis(
                        "what_if",
                        removed_interests=["data"],
                        removed_skills=["statistics"],
                        hypothetical=True,
                    )
                ]
            )
        )
        conversation_id, profile_id = self.rich_conversation()
        self.send(conversation_id, "What if I didn't like statistics?")

        profile = self.db_profile(profile_id)
        self.assertIn("statistics", {row.skill_id for row in profile.skills})
        self.assertIn("data", {row.tag for row in profile.tags})

    def test_a_hypothetical_is_scored_separately(self):
        llm = FakeLLM(
            analyses=[
                analysis("what_if", removed_interests=["ai_ml"], hypothetical=True)
            ]
        )
        self.use_llm(llm)
        conversation_id, _ = self.rich_conversation()
        self.send(conversation_id, "What if AI stopped interesting me?")

        briefing = llm.last_briefing
        self.assertEqual(briefing["scenario"]["removed_interests"], ["ai_ml"])
        self.assertIn("Hypothetical only", briefing["scenario"]["note"])
        # The stored ranking and the hypothetical one are both present and are
        # computed from different profiles, so they must not be identical.
        stored = briefing["recommendations"]["matches"]
        scenario = briefing["scenario_recommendations"]["matches"]
        self.assertNotEqual(
            [m["match_score"] for m in stored], [m["match_score"] for m in scenario]
        )


# --------------------------------------------------------------------------
# hallucination control
# --------------------------------------------------------------------------

class TestTheEngineOwnsTheNumbers(ConversationTestCase):
    def test_an_invented_skill_never_reaches_the_profile(self):
        payload = analysis(skills=[("python", 0.8, "explicit")])
        payload["profile_updates"]["skills"].append(
            {"skill_id": "pytorch", "proficiency": 0.9, "source": "explicit"}
        )
        self.use_llm(FakeLLM(analyses=[payload]))
        conversation_id, profile_id = self.start_conversation()

        body = self.send(conversation_id, "I know Python and PyTorch.")

        stored = {row.skill_id for row in self.db_profile(profile_id).skills}
        self.assertEqual(stored, {"python"})
        self.assertEqual([s["skill_id"] for s in body["profile"]["skills"]], ["python"])

    def test_an_invented_career_is_never_put_in_focus(self):
        llm = FakeLLM(
            analyses=[
                empty_analysis("why_recommendation")
                | {"careers_mentioned": ["quantum_alchemist"]}
            ]
        )
        self.use_llm(llm)
        conversation_id, _ = self.rich_conversation()
        self.send(conversation_id, "Why quantum alchemy?")

        briefing = llm.last_briefing
        self.assertNotIn("quantum_alchemist", str(briefing))

    def test_an_invented_score_field_is_rejected_and_the_profile_survives(self):
        """A payload carrying engine-owned data is not partially trusted."""
        payload = analysis(skills=[("python", 0.8, "explicit")])
        payload["profile_updates"]["career_scores"] = {"ml_engineer": 99}
        self.use_llm(FakeLLM(analyses=[payload]))
        conversation_id, profile_id = self.start_conversation()

        body = self.send(conversation_id, "I know Python.")

        self.assertEqual(self.db_profile(profile_id).skills, [])
        self.assertEqual(body["status"], "ok")  # the conversation still continues

    def test_the_briefing_only_ever_contains_engine_output(self):
        llm = FakeLLM(analyses=[empty_analysis("career_recommendation")])
        self.use_llm(llm)
        conversation_id, profile_id = self.rich_conversation()
        self.send(conversation_id, "What suits me?")

        expected = recommend_careers(
            to_student_profile(self.db_profile(profile_id), self.kb), self.kb, limit=3
        )
        briefed = {m["career_id"] for m in llm.last_briefing["recommendations"]["matches"]}
        self.assertEqual(briefed, {m.career_id for m in expected.matches})


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------

class TestFailureHandling(ConversationTestCase):
    def test_a_provider_failure_returns_a_fallback_and_persists_nothing(self):
        self.use_llm(FakeLLM(fail_json=True))
        conversation_id, profile_id = self.start_conversation()

        body = self.send(conversation_id, "I like Python and AI.")

        self.assertEqual(body["status"], "llm_error")
        self.assertFalse(body["message"]["persisted"])
        self.assertIsNone(body["message"]["id"])
        self.assertEqual([m["role"] for m in self.history(conversation_id)], ["user"])

    def test_the_user_message_survives_a_provider_failure(self):
        self.use_llm(FakeLLM(fail_json=True))
        conversation_id, _ = self.start_conversation()

        self.send(conversation_id, "I like Python and AI.")

        messages = self.history(conversation_id)
        self.assertEqual(messages[0]["content"], "I like Python and AI.")

    def test_a_failed_reply_call_does_not_persist_an_assistant_turn(self):
        self.use_llm(
            FakeLLM(analyses=[analysis(skills=[("python", 0.8, "explicit")])], fail_text=True)
        )
        conversation_id, profile_id = self.start_conversation()

        body = self.send(conversation_id, "I know Python.")

        self.assertEqual(body["status"], "llm_error")
        self.assertFalse(body["message"]["persisted"])
        self.assertEqual([m["role"] for m in self.history(conversation_id)], ["user"])
        # What was understood before the failure is still true, so it is kept.
        self.assertEqual(
            {row.skill_id for row in self.db_profile(profile_id).skills}, {"python"}
        )

    def test_a_fallback_never_shows_provider_internals(self):
        self.use_llm(FakeLLM(fail_json=True))
        conversation_id, _ = self.start_conversation()

        content = self.send(conversation_id, "hello")["message"]["content"].lower()

        for leak in ("gemini", "google", "api", "token", "traceback", "exception", "key"):
            self.assertNotIn(leak, content)

    def test_an_unusable_extraction_leaves_the_profile_untouched(self):
        self.use_llm(FakeLLM(analyses=[{"intent": "not_a_real_intent"}]))
        conversation_id, profile_id = self.rich_conversation()
        before = {row.skill_id: row.proficiency for row in self.db_profile(profile_id).skills}

        body = self.send(conversation_id, "mmmph")

        after = {row.skill_id: row.proficiency for row in self.db_profile(profile_id).skills}
        self.assertEqual(before, after)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["intent"], "general_conversation")

    def test_an_unusable_extraction_tells_the_model_to_ask_again(self):
        llm = FakeLLM(analyses=[{"nonsense": True}])
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "mmmph")

        notes = " ".join(llm.last_briefing["notes"]).lower()
        self.assertIn("another way", notes)


# --------------------------------------------------------------------------
# persistence of the counsellor's own turn
# --------------------------------------------------------------------------

class TestAssistantPersistence(ConversationTestCase):
    def test_a_real_reply_is_written_to_the_history(self):
        self.use_llm(FakeLLM(replies=["What kind of work do you enjoy?"]))
        conversation_id, _ = self.start_conversation()

        body = self.send(conversation_id, "I don't know what I want to do.")

        self.assertTrue(body["message"]["persisted"])
        self.assertIsInstance(body["message"]["id"], int)
        messages = self.history(conversation_id)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "What kind of work do you enjoy?")

    def test_the_stored_reply_is_the_one_returned(self):
        self.use_llm(FakeLLM(replies=["Exactly these words."]))
        conversation_id, _ = self.start_conversation()

        body = self.send(conversation_id, "hello")

        stored = self.session.get(models.Message, body["message"]["id"])
        self.assertEqual(stored.content, body["message"]["content"])
        self.assertEqual(stored.role, "assistant")

    def test_turns_accumulate_in_order(self):
        self.use_llm(FakeLLM(replies=["one", "two"]))
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "first")
        self.send(conversation_id, "second")

        messages = self.history(conversation_id)
        self.assertEqual(
            [m["content"] for m in messages], ["first", "one", "second", "two"]
        )


# --------------------------------------------------------------------------
# what the model is told
# --------------------------------------------------------------------------

class TestPromptContent(ConversationTestCase):
    def test_the_reply_prompt_forbids_inventing_numbers(self):
        llm = FakeLLM()
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "hello")

        prompt = llm.text_systems[-1]
        self.assertIn("briefing is the only source of numbers", prompt.lower())
        self.assertIn("BRIEFING", prompt)

    def test_the_briefing_is_valid_json(self):
        llm = FakeLLM()
        self.use_llm(llm)
        conversation_id, _ = self.rich_conversation()
        self.send(conversation_id, "hello")

        briefing = briefing_in(llm.text_systems[-1])
        self.assertIn("known_profile", briefing)
        self.assertIn("intent", briefing)

    def test_the_analysis_call_is_schema_constrained(self):
        llm = FakeLLM()
        self.use_llm(llm)
        conversation_id, _ = self.start_conversation()
        self.send(conversation_id, "hello")

        schema = llm.json_schemas[-1]
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("intent", schema["required"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
