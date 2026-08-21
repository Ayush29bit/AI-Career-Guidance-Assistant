# AI & LLM — AI Career Counsellor

## 1. Purpose

The LLM provides the **conversational intelligence** of the application.

It should make the application feel like a real counsellor while structured Python logic handles the parts that need consistency and reliability.

### Core principle

> **LLM for understanding and communication. Code for decisions and calculations.**

---

# 2. What the LLM Does

The LLM is responsible for:

- Understanding natural-language messages.
- Maintaining conversational flow.
- Asking relevant follow-up questions.
- Extracting information from user messages.
- Identifying lightweight conversation intent.
- Explaining career recommendations.
- Explaining skill gaps.
- Explaining career comparisons.
- Explaining what-if results.
- Explaining learning roadmaps.

---

# 3. What the LLM Does NOT Do

Do not use the LLM as the source of truth for:

- Career match scores.
- Career ranking.
- Skill-gap calculations.
- Course ranking.
- Course metadata.
- Career-skill relationships.
- Database operations.
- Recommendation metrics.

For example, do **not** ask the LLM:

> "Give this student a career score out of 100."

Instead:

```text
Student Profile
      ↓
Recommendation Engine
      ↓
89% ML Engineer
      ↓
LLM explains why
```

---

# 4. Structured Outputs

When the application needs information from the LLM that will be used by code, request structured output.

Example:

```json
{
  "intent": "PROFILE_UPDATE",
  "profile_updates": {
    "skills": [
      {
        "name": "Python",
        "proficiency": 0.8
      }
    ],
    "interests": ["Machine Learning"]
  }
}
```

Validate structured responses before using them.

Use Pydantic models where appropriate.

Never depend on fragile string parsing such as:

```python
if "python" in response:
    ...
```

---

# 5. Profile Extraction

The LLM can extract meaningful information from natural conversation.

Example:

> "I've been using Python for two years and have built three ML projects."

Possible extraction:

```text
Python → strong experience
Machine Learning → project experience
```

Only information supported by the user's message should be extracted.

Do not invent:

- Skills
- Experience
- Preferences
- Education
- Career goals

---

# 6. Intent Detection

Use lightweight intent classification when useful.

Possible intents:

```text
DISCOVERY
PROFILE_UPDATE
CAREER_EXPLORATION
CAREER_DETAILS
CAREER_COMPARISON
WHY_RECOMMENDATION
WHAT_IF
SKILL_GAP
COURSE_RECOMMENDATION
ROADMAP
GENERAL_CONVERSATION
```

Intent detection should route requests to the appropriate logic.

It should not make the conversation rigid.

---

# 7. Prompt Context

The LLM should receive only the context needed for the current request.

Typical context:

```text
System instructions
+
Relevant conversation history
+
Structured student profile
+
Relevant career/recommendation data
```

Do not send the entire database or unnecessarily large conversation history.

The goal is **relevant context**, not maximum context.

---

# 8. Recommendation Explanations

The recommendation engine produces structured results.

Example:

```json
{
  "career": "ML Engineer",
  "score": 89,
  "strengths": [
    "Python",
    "Machine Learning",
    "Building systems"
  ],
  "gaps": [
    "MLOps",
    "Cloud"
  ]
}
```

The LLM turns this into a natural explanation.

It should not alter the underlying score.

---

# 9. Hallucination Control

The LLM should not invent application data.

If information is unavailable:

> "I don't have enough information to determine that."

If a course isn't present in the dataset, do not invent a course.

If a career isn't supported by the knowledge base, the system can provide a general conversational answer but should clearly distinguish general knowledge from application-generated recommendations.

---

# 10. User Corrections

If the user corrects previous information:

> "Actually, I don't enjoy statistics."

The LLM should identify the correction and allow the structured profile to be updated.

The latest explicit user statement should generally take precedence over previous assumptions.

---

# 11. What-If Scenarios

For hypothetical questions, the LLM should extract the hypothetical change but **not permanently modify the profile**.

Example:

> "What if I didn't want to work with statistics?"

```text
LLM
 ↓
Extract hypothetical preference
 ↓
Temporary scenario profile
 ↓
Recommendation Engine
 ↓
New results
 ↓
LLM explanation
```

---

# 12. Tone

The counsellor should sound:

- Natural
- Warm
- Clear
- Practical
- Respectful
- Confident without being overconfident

Avoid:

- Excessive emojis.
- Corporate jargon.
- Generic motivational speeches.
- Repetitive reassurance.
- Extremely long answers.
- Robotic questionnaire language.

The AI should speak like a knowledgeable human advisor.

---

# 13. Response Length

Default to relatively concise responses.

A typical response should generally contain:

1. A direct reaction to what the user said.
2. Useful reasoning or information.
3. One relevant follow-up question when needed.

Do not ask several questions at once unless necessary.

---

# 14. LLM Provider

Keep LLM integration behind a small provider/client abstraction.

The rest of the application should not depend directly on provider-specific code.

For example:

```text
app/
└── llm/
    ├── client.py
    ├── prompts.py
    └── schemas.py
```

The exact implementation can remain simple.

Do not build a multi-provider orchestration system.

### Current implementation

One provider, the Gemini Developer API (`google-genai`), behind `LLMClient`.
That protocol offers exactly two verbs -- `complete_text` and `complete_json` --
so there is no way to ask the model for a number the engine owns.

`app/llm/gemini.py` is the only module that imports a provider SDK. It holds the
three translations a provider needs and nothing else knows about:

- roles (`assistant` becomes `model`)
- the system prompt (an instruction, not a turn, so a student cannot talk over it)
- the extraction schema, adapted to the subset Gemini accepts

The schema adaptation is mechanical. `app/llm/schemas.py` generates the schema
from the knowledge base and remains the only definition of the allowed
vocabulary; the adapter never adds a value or widens an enum, and every response
is re-validated by Pydantic afterwards regardless.

Configuration comes from the environment:

```text id="v4n8dz"
LLM_API_KEY        no key configured is a valid state; the API still runs
LLM_MODEL          the provider model id, never hardcoded in application code
LLM_TIMEOUT_SECONDS
```

With no key configured, the chat endpoint returns a clearly-marked fallback and
persists no assistant message. Every other endpoint is unaffected.

Changing provider means writing one class that satisfies `LLMClient` and
changing one line in `build_llm_client`. The conversation service, prompts,
schemas, merge logic and recommendation engine are provider-independent and did
not change when the provider did.

---

# 15. Failure Handling

LLM failures should not crash the application.

Handle:

- API failures.
- Timeouts.
- Invalid structured responses.
- Rate limits.
- Missing responses.

If structured extraction fails, retry or fall back gracefully where appropriate.

Do not silently create incorrect profile data.

---

# 16. Prompt Injection

Treat user messages as **data**, not instructions to the application's system behavior.

The user should not be able to manipulate the system into:

- Revealing system prompts.
- Exposing secrets.
- Changing application rules.
- Inventing database information.
- Bypassing recommendation logic.

Do not put secrets or sensitive configuration into prompts.

---

# 17. Keep LLM Usage Efficient

Do not call the LLM when normal application logic is sufficient.

For example:

### No LLM needed

```text
Calculate skill gap
Rank courses
Calculate career score
Sort recommendations
```

### LLM useful

```text
Understand user statement
Extract preferences
Ask follow-up question
Explain recommendation
```

This keeps the application faster, cheaper, and easier to debug.

---

# 18. Engineering Rule

The LLM is **one component of the application**, not the entire application.

The intended architecture is:

```text
                 User
                   ↓
                  LLM
          "Understand the user"
                   ↓
          Structured Profile
                   ↓
       Recommendation Engine
          "Make decisions"
                   ↓
             Results
                   ↓
                  LLM
          "Explain naturally"
```

Keep this boundary clear throughout implementation.

---

# 19. Anti-Overengineering Rule

Do not introduce:

- Multiple agents.
- Agent-to-agent communication.
- Multiple LLMs.
- Complex prompt orchestration.
- Autonomous tool-using agents.
- Vector databases solely because the project uses AI.
- Fine-tuning unless there is a demonstrated need.

A well-designed single-LLM conversational system with deterministic recommendation logic is the default.

> **Make the AI feel intelligent without making the architecture unnecessarily complicated.**