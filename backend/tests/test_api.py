"""API tests.

These need a reachable PostgreSQL with the migration applied, so the whole
module skips cleanly when there is not one -- the other tests must stay runnable
on a machine with no database.

Isolation comes from a transaction that is never committed. Each test opens a
connection, begins a transaction, and hands the endpoints a Session bound to it
with `join_transaction_mode="create_savepoint"`, so an endpoint's `commit()`
releases a savepoint rather than ending the outer transaction. Rolling that
transaction back in tearDown leaves the database exactly as the test found it,
seeded knowledge tables included.
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
    from app.database import models
    from app.recommendation.engine import recommend_careers

API = "/api/v1"

#: A profile rich enough to clear the cold-start floor (3 skills, 1 interest,
#: 1 work preference) and produce a real ranking.
RICH_SKILLS = {"python": 0.8, "machine_learning": 0.7, "sql": 0.6, "statistics": 0.4}
RICH_INTERESTS = ("ai_ml", "data")
RICH_PREFERENCES = ("building", "experimentation")


@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class ApiTestCase(unittest.TestCase):
    """Base class: a client whose every write is rolled back afterwards."""

    def setUp(self):
        self.connection = get_engine().connect()
        self.transaction = self.connection.begin()
        self.session = Session(
            bind=self.connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        _app.dependency_overrides[get_session] = lambda: self.session
        # No LLM, always. A test must never reach a provider, and it must not
        # depend on whether the developer running it happens to have a key in
        # .env -- which would make the whole suite behave differently on two
        # machines. Tests that want a counsellor turn inject a fake instead
        # (test_conversation.py).
        _app.dependency_overrides[get_llm_client] = lambda: None
        self.client = TestClient(_app)

    def tearDown(self):
        _app.dependency_overrides.clear()
        self.session.close()
        self.transaction.rollback()
        self.connection.close()

    # -- helpers ----------------------------------------------------------

    def start_conversation(self) -> tuple[str, str]:
        response = self.client.post(f"{API}/conversations")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        return body["conversation_id"], body["profile_id"]

    def populate(
        self,
        profile_id: str,
        *,
        skills: dict[str, float] | None = None,
        interests: tuple[str, ...] = (),
        work_preferences: tuple[str, ...] = (),
        strengths: tuple[str, ...] = (),
        dislikes: tuple[str, ...] = (),
        goals: tuple[str, ...] = (),
        experience_level: str | None = None,
    ) -> None:
        profile = self.session.get(models.StudentProfile, uuid.UUID(profile_id))
        profile.experience_level = experience_level
        for skill_id, proficiency in (skills or {}).items():
            self.session.add(
                models.StudentProfileSkill(
                    student_profile_id=profile.id, skill_id=skill_id, proficiency=proficiency
                )
            )
        for tag_type, tags in (("interest", interests), ("work_preference", work_preferences)):
            for tag in tags:
                self.session.add(
                    models.StudentProfileTag(
                        student_profile_id=profile.id, tag_type=tag_type, tag=tag
                    )
                )
        for entry_type, values in (
            ("strength", strengths),
            ("dislike", dislikes),
            ("goal", goals),
        ):
            for content in values:
                self.session.add(
                    models.StudentProfileEntry(
                        student_profile_id=profile.id, entry_type=entry_type, content=content
                    )
                )
        self.session.flush()
        self.session.expire(profile)

    def rich_profile(self) -> str:
        _, profile_id = self.start_conversation()
        self.populate(
            profile_id,
            skills=dict(RICH_SKILLS),
            interests=RICH_INTERESTS,
            work_preferences=RICH_PREFERENCES,
            experience_level="intermediate",
        )
        return profile_id


class TestHealth(ApiTestCase):
    def test_health_reports_ok(self):
        response = self.client.get(f"{API}/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ok")
        self.assertEqual(body["knowledge_base"], "ok")

    def test_health_reports_the_loaded_knowledge_base(self):
        body = self.client.get(f"{API}/health").json()
        self.assertEqual(body["careers"], 11)
        self.assertEqual(body["skills"], 61)

    def test_health_leaks_no_connection_details(self):
        from app.config import get_settings

        raw = self.client.get(f"{API}/health").text.lower()
        url = get_settings().database_url
        for secret in (url, url.split("@")[0].split(":")[-1], "password", "5432"):
            self.assertNotIn(secret.lower(), raw)

    def test_lifespan_starts_and_stops(self):
        with TestClient(_app) as client:
            self.assertEqual(client.get(f"{API}/health").status_code, 200)


class TestConversationCreation(ApiTestCase):
    def test_creating_a_conversation_returns_both_ids(self):
        response = self.client.post(f"{API}/conversations")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        uuid.UUID(body["conversation_id"])
        uuid.UUID(body["profile_id"])
        self.assertIn("created_at", body)

    def test_creating_a_conversation_persists_a_profile(self):
        conversation_id, profile_id = self.start_conversation()
        conversation = self.session.get(models.Conversation, uuid.UUID(conversation_id))
        self.assertIsNotNone(conversation)
        self.assertEqual(str(conversation.student_profile_id), profile_id)
        self.assertIsNotNone(self.session.get(models.StudentProfile, uuid.UUID(profile_id)))

    def test_a_new_profile_starts_genuinely_empty(self):
        _, profile_id = self.start_conversation()
        body = self.client.get(f"{API}/profile/{profile_id}").json()
        self.assertIsNone(body["experience_level"])
        self.assertEqual(body["skills"], [])
        self.assertEqual(body["signal_count"], 0)
        self.assertFalse(body["ready_for_recommendations"])

    def test_each_conversation_is_distinct(self):
        first, _ = self.start_conversation()
        second, _ = self.start_conversation()
        self.assertNotEqual(first, second)


class TestConversationRetrieval(ApiTestCase):
    def test_new_conversation_has_no_messages(self):
        conversation_id, profile_id = self.start_conversation()
        body = self.client.get(f"{API}/conversations/{conversation_id}").json()
        self.assertEqual(body["conversation_id"], conversation_id)
        self.assertEqual(body["profile_id"], profile_id)
        self.assertEqual(body["message_count"], 0)
        self.assertEqual(body["messages"], [])

    def test_missing_conversation_is_404(self):
        response = self.client.get(f"{API}/conversations/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"])

    def test_invalid_uuid_is_422(self):
        response = self.client.get(f"{API}/conversations/not-a-uuid")
        self.assertEqual(response.status_code, 422)


class TestMessages(ApiTestCase):
    """The message endpoint with no LLM configured.

    These tests deliberately leave `get_llm_client` un-overridden, so the turn
    takes the "no LLM available" path. That is what exercises the guarantee the
    endpoint has to keep under failure: the student's words are stored, and
    nothing is invented on the counsellor's behalf. The conversational turn
    itself is tested against a fake client in test_conversation.py.
    """

    def test_posting_a_message_persists_it(self):
        conversation_id, _ = self.start_conversation()
        response = self.client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "I like Python and AI but I'm not sure what to choose."},
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["user_message"]["role"], "user")
        self.assertEqual(
            body["user_message"]["content"],
            "I like Python and AI but I'm not sure what to choose.",
        )
        self.assertIsInstance(body["user_message"]["id"], int)

    def test_fallback_reply_is_marked_and_not_persisted(self):
        conversation_id, _ = self.start_conversation()
        body = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": "hello"}
        ).json()
        self.assertEqual(body["status"], "llm_unavailable")
        self.assertEqual(body["message"]["role"], "assistant")
        self.assertFalse(body["message"]["persisted"])
        self.assertIsNone(body["message"]["id"])
        self.assertTrue(body["message"]["content"])

        history = self.client.get(f"{API}/conversations/{conversation_id}").json()
        self.assertEqual(history["message_count"], 1)
        self.assertEqual([m["role"] for m in history["messages"]], ["user"])

    def test_fallback_turn_still_reports_the_profile(self):
        conversation_id, profile_id = self.start_conversation()
        body = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": "hello"}
        ).json()
        self.assertEqual(body["profile"]["profile_id"], profile_id)
        self.assertFalse(body["profile"]["ready_for_recommendations"])
        self.assertIsNone(body["recommendations"])

    def test_messages_come_back_in_order(self):
        conversation_id, _ = self.start_conversation()
        for text_ in ("first", "second", "third"):
            self.client.post(
                f"{API}/conversations/{conversation_id}/messages", json={"content": text_}
            )
        history = self.client.get(f"{API}/conversations/{conversation_id}").json()
        self.assertEqual(history["message_count"], 3)
        self.assertEqual(
            [m["content"] for m in history["messages"]], ["first", "second", "third"]
        )
        ids = [m["id"] for m in history["messages"]]
        self.assertEqual(ids, sorted(ids))

    def test_content_is_trimmed(self):
        conversation_id, _ = self.start_conversation()
        body = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": "  hi  "}
        ).json()
        self.assertEqual(body["user_message"]["content"], "hi")

    def test_blank_message_is_rejected(self):
        conversation_id, _ = self.start_conversation()
        for content in ("", "   ", "\n\t "):
            with self.subTest(content=repr(content)):
                response = self.client.post(
                    f"{API}/conversations/{conversation_id}/messages", json={"content": content}
                )
                self.assertEqual(response.status_code, 422)

    def test_oversized_message_is_rejected(self):
        conversation_id, _ = self.start_conversation()
        response = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": "x" * 4001}
        )
        self.assertEqual(response.status_code, 422)

    def test_missing_content_field_is_rejected(self):
        conversation_id, _ = self.start_conversation()
        response = self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={}
        )
        self.assertEqual(response.status_code, 422)

    def test_message_to_a_missing_conversation_is_404(self):
        response = self.client.post(
            f"{API}/conversations/{uuid.uuid4()}/messages", json={"content": "hello"}
        )
        self.assertEqual(response.status_code, 404)

    def test_nothing_is_stored_for_a_rejected_message(self):
        conversation_id, _ = self.start_conversation()
        self.client.post(
            f"{API}/conversations/{conversation_id}/messages", json={"content": "   "}
        )
        history = self.client.get(f"{API}/conversations/{conversation_id}").json()
        self.assertEqual(history["message_count"], 0)


class TestProfile(ApiTestCase):
    def test_profile_returns_everything_stored(self):
        _, profile_id = self.start_conversation()
        self.populate(
            profile_id,
            skills={"python": 0.8, "sql": 0.6},
            interests=("ai_ml",),
            work_preferences=("building",),
            strengths=("Ships side projects",),
            dislikes=("Heavy statistics",),
            goals=("Work on AI products",),
            experience_level="intermediate",
        )
        body = self.client.get(f"{API}/profile/{profile_id}").json()

        self.assertEqual(body["experience_level"], "intermediate")
        self.assertEqual(body["interests"], ["ai_ml"])
        self.assertEqual(body["work_preferences"], ["building"])
        self.assertEqual(body["strengths"], ["Ships side projects"])
        self.assertEqual(body["dislikes"], ["Heavy statistics"])
        self.assertEqual(body["goals"], ["Work on AI products"])
        self.assertEqual({s["skill_id"] for s in body["skills"]}, {"python", "sql"})

    def test_skills_carry_display_metadata_and_are_sorted(self):
        _, profile_id = self.start_conversation()
        self.populate(profile_id, skills={"sql": 0.6, "python": 0.8})
        skills = self.client.get(f"{API}/profile/{profile_id}").json()["skills"]
        self.assertEqual([s["skill_id"] for s in skills], ["python", "sql"])
        self.assertEqual(skills[0]["name"], "Python")
        self.assertEqual(skills[0]["kind"], "language")
        self.assertEqual(skills[0]["category"], "programming")

    def test_cold_start_is_reported_with_what_is_missing(self):
        _, profile_id = self.start_conversation()
        self.populate(profile_id, skills={"python": 0.8})
        body = self.client.get(f"{API}/profile/{profile_id}").json()
        self.assertFalse(body["ready_for_recommendations"])
        missing = {m["field_name"]: m for m in body["missing_information"]}
        self.assertEqual(missing["skills"], {"field_name": "skills", "have": 1, "need": 3})
        self.assertIn("interests", missing)
        self.assertIn("work_preferences", missing)

    def test_a_complete_profile_is_ready(self):
        profile_id = self.rich_profile()
        body = self.client.get(f"{API}/profile/{profile_id}").json()
        self.assertTrue(body["ready_for_recommendations"])
        self.assertEqual(body["missing_information"], [])

    def test_missing_profile_is_404(self):
        response = self.client.get(f"{API}/profile/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_invalid_uuid_is_422(self):
        self.assertEqual(self.client.get(f"{API}/profile/nope").status_code, 422)

    def test_a_profile_inconsistent_with_the_taxonomy_fails_safely(self):
        """An interest tag has no foreign key, so drift is possible; it must be loud."""
        _, profile_id = self.start_conversation()
        self.session.add(
            models.StudentProfileTag(
                student_profile_id=uuid.UUID(profile_id),
                tag_type="interest",
                tag="underwater_basket_weaving",
            )
        )
        self.session.flush()

        # The specific problem is logged for the developer; assertLogs both
        # verifies that and keeps it out of the test output.
        with self.assertLogs("app.main", level="ERROR") as logged:
            response = self.client.get(f"{API}/profile/{profile_id}")

        self.assertEqual(response.status_code, 500)
        detail = response.json()["detail"]
        self.assertNotIn("underwater_basket_weaving", detail)
        self.assertIn("not consistent", detail)
        # ...but it does reach the log, where a developer can act on it
        self.assertIn("underwater_basket_weaving", "".join(logged.output))


class TestRecommendations(ApiTestCase):
    def test_cold_profile_returns_insufficient_profile_not_an_error(self):
        _, profile_id = self.start_conversation()
        response = self.client.get(f"{API}/profile/{profile_id}/recommendations")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "insufficient_profile")
        self.assertEqual(body["matches"], [])
        self.assertEqual(
            {m["field_name"] for m in body["missing_information"]},
            {"skills", "interests", "work_preferences"},
        )
        self.assertEqual(body["considered_careers"], 11)

    def test_rich_profile_is_ranked(self):
        profile_id = self.rich_profile()
        body = self.client.get(f"{API}/profile/{profile_id}/recommendations").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["matches"]), 5)
        scores = [m["score"] for m in body["matches"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for match in body["matches"]:
            self.assertGreaterEqual(match["score_100"], 0)
            self.assertLessEqual(match["score_100"], 100)

    def test_the_api_returns_exactly_what_the_engine_computed(self):
        """No scoring may happen in the API layer."""
        profile_id = self.rich_profile()
        body = self.client.get(f"{API}/profile/{profile_id}/recommendations?limit=11").json()

        db_profile = self.session.get(models.StudentProfile, uuid.UUID(profile_id))
        self.session.refresh(db_profile)
        expected = recommend_careers(
            to_student_profile(db_profile, get_knowledge_base()),
            get_knowledge_base(),
            limit=11,
        )
        self.assertEqual(
            [(m["career_id"], m["score"]) for m in body["matches"]],
            [(m.career_id, m.score) for m in expected.matches],
        )

    def test_the_explainability_payload_is_present(self):
        profile_id = self.rich_profile()
        top = self.client.get(f"{API}/profile/{profile_id}/recommendations").json()["matches"][0]

        breakdown = top["breakdown"]
        self.assertTrue(breakdown["experience_known"])
        recomputed = (
            0.50 * breakdown["skill_match"]
            + 0.25 * breakdown["interest_match"]
            + 0.15 * breakdown["preference_match"]
            + 0.10 * breakdown["experience_fit"]
        )
        self.assertAlmostEqual(top["score"], recomputed, places=9)

        self.assertTrue(top["strengths"])
        self.assertIn("career_name", top)
        self.assertIn("short_description", top)
        for gap in top["skill_gaps"]:
            self.assertFalse(gap["is_human_skill"])
            self.assertAlmostEqual(gap["priority"], gap["gap"] * gap["importance"], places=9)

    def test_human_skill_gaps_are_kept_separate(self):
        profile_id = self.rich_profile()
        matches = self.client.get(f"{API}/profile/{profile_id}/recommendations").json()["matches"]
        for match in matches:
            self.assertTrue(all(not g["is_human_skill"] for g in match["skill_gaps"]))
            self.assertTrue(all(g["is_human_skill"] for g in match["human_skill_gaps"]))

    def test_limit_is_respected_and_validated(self):
        profile_id = self.rich_profile()
        base = f"{API}/profile/{profile_id}/recommendations"
        self.assertEqual(len(self.client.get(f"{base}?limit=3").json()["matches"]), 3)
        # the engine never invents careers beyond the eleven it knows
        self.assertEqual(len(self.client.get(f"{base}?limit=20").json()["matches"]), 11)
        self.assertEqual(self.client.get(f"{base}?limit=0").status_code, 422)
        self.assertEqual(self.client.get(f"{base}?limit=abc").status_code, 422)

    def test_missing_profile_is_404(self):
        response = self.client.get(f"{API}/profile/{uuid.uuid4()}/recommendations")
        self.assertEqual(response.status_code, 404)

    def test_results_are_deterministic_across_requests(self):
        profile_id = self.rich_profile()
        url = f"{API}/profile/{profile_id}/recommendations"
        first = self.client.get(url).json()
        second = self.client.get(url).json()
        self.assertEqual(first, second)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
