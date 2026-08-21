# AI Career Counsellor — Architecture

## 1. Architecture Goal

Build a **simple, clean modular monolith** that is fast to develop and easy for a small team to understand.

This is a hackathon prototype, not a large-scale production system.

### Core principle

> **Use the simplest architecture that reliably delivers the required user experience.**

Do not introduce microservices, Kubernetes, message queues, complex agent systems, or additional databases unless a real feature requires them.

---

# 2. High-Level Architecture

```text id="2z9c4u"
                  ┌─────────────────────┐
                  │   React + Vite      │
                  │ Conversation UI     │
                  └──────────┬──────────┘
                             │
                          HTTP / SSE
                             │
                             ▼
                  ┌─────────────────────┐
                  │      FastAPI        │
                  │      Backend       │
                  └──────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
       Conversation     Recommendation    LLM
          Logic             Engine
              │              │
              └───────┬──────┘
                      │
                      ▼
               ┌─────────────┐
               │ PostgreSQL  │
               └──────┬──────┘
                      │
              ┌───────┴────────┐
              │                │
              ▼                ▼
        Career/Skill       Coursera
        Knowledge Base      Dataset
```

---

# 3. Frontend

### Technology

- React
- Vite
- Tailwind CSS

### Main responsibility

Provide a conversation-first interface.

Primary UI:

- Chat/conversation
- Live student profile
- Career recommendations
- Career comparison
- Skill gaps
- Course recommendations
- Roadmap

The user should primarily interact through conversation.

Buttons/cards can support the experience but should not replace natural conversation.

---

# 4. Backend

### Technology

**FastAPI + Python**

The backend handles:

- Conversation requests.
- LLM interaction.
- Student profile updates.
- Recommendation logic.
- Skill-gap calculation.
- Course ranking.
- Career comparison.
- What-if scenarios.
- Roadmap generation.
- Database access.

Keep the backend as **one application**.

Do not split these into separate deployable services.

---

# 5. Backend Logical Modules

Use a small number of meaningful modules.

```text id="m7q3k0"
backend/
└── app/
    ├── api/
    ├── conversation/
    ├── recommendation/
    ├── llm/
    ├── database/
    └── main.py
```

The exact structure can evolve if a simpler structure is better.

Avoid creating a separate abstraction for every function.

---

# 6. Conversation Flow

A typical message follows:

```text id="f2s9f1"
User message
      ↓
FastAPI
      ↓
Conversation logic
      ↓
Load conversation + student profile
      ↓
LLM understands message
      ↓
Profile / intent extraction
      ↓
Recommendation logic if required
      ↓
LLM generates response
      ↓
Save relevant state
      ↓
Return response
```

For normal conversation, avoid unnecessary calls to recommendation logic.

Only run recommendation-related logic when the conversation actually requires it.

---

# 7. Two Intelligence Layers

The system deliberately separates **conversation intelligence** from **recommendation logic**.

## LLM

Responsible for:

- Understanding natural language.
- Asking follow-up questions.
- Extracting profile information.
- Understanding intent.
- Explaining recommendations.
- Conversational counselling.

## Recommendation Engine

Responsible for:

- Career scoring.
- Career ranking.
- Skill matching.
- Skill-gap calculation.
- Course ranking.
- Career comparison.
- What-if recalculation.

### Rule

> **The LLM should not invent recommendation scores.**

The recommendation engine produces structured results.

The LLM turns those results into natural language.

---

# 8. Student Profile Flow

```text id="j7n1mj"
Conversation
     ↓
LLM extraction
     ↓
Structured profile
     ↓
PostgreSQL
```

The profile should contain useful information such as:

- Skills
- Skill proficiency/confidence
- Interests
- Preferences
- Strengths
- Dislikes
- Experience
- Goals

Do not store every conversational statement as a separate profile attribute.

Only meaningful information should become structured profile data.

---

# 9. Recommendation Flow

```text id="iyl4gd"
Student Profile
      ↓
Career Knowledge Base
      ↓
Career Matching
      ↓
Career Scores
      ↓
Top Career Recommendations
```

The recommendation engine should consider factors such as:

- Skill match
- Interest match
- Preference match
- Experience fit

The exact scoring approach is defined in `RECOMMENDATIONS.md`.

---

# 10. Skill Gap Flow

```text id="g4m0s1"
Selected Career
      ↓
Required Skills
      ↓
Compare with Student Skills
      ↓
Calculate Gaps
      ↓
Prioritize Gaps
```

The result is used by both:

- The UI.
- Course recommendation.
- Roadmap generation.
- Conversational explanations.

---

# 11. Course Recommendation Flow

```text id="6rv3he"
Skill Gap
    ↓
Required/Missing Skills
    ↓
Coursera Course-Skill Data
    ↓
Course Ranking
    ↓
Recommended Courses
```

The Coursera dataset is a **learning-resource source**.

It is not responsible for determining whether a student should become a particular career.

---

# 12. What-If Flow

"What-if" scenarios should be handled without permanently modifying the user's profile unless confirmed.

```text id="z7d3w4"
User:
"What if I don't want heavy statistics?"

        ↓

Temporary profile change

        ↓

Recommendation Engine

        ↓

New career ranking

        ↓

LLM explains the difference
```

---

# 13. Career Comparison Flow

```text id="3bl2we"
User asks comparison
        ↓
Retrieve career data
        ↓
Compare against student profile
        ↓
Generate structured comparison
        ↓
LLM explains conversationally
```

---

# 14. Database

Use **PostgreSQL**.

It should store:

- Student profiles
- Conversations
- Messages
- Careers
- Skills
- Career-skill relationships
- Courses
- Course-skill relationships
- Recommendation results where useful

PostgreSQL is sufficient for the prototype.

Do not introduce MongoDB, Neo4j, Pinecone, Qdrant, or another database unless a demonstrated requirement appears.

---

# 15. Vector Search

Vector search is **not required by default**.

The primary data is structured:

```text
Career → Skills
Course → Skills
Student → Skills
```

Normal PostgreSQL queries are sufficient.

If semantic search later provides a clear improvement, use **pgvector** before introducing a separate vector database.

---

# 16. Streaming

Use **Server-Sent Events (SSE)** if streaming improves the conversational experience.

The purpose is mainly to make AI responses feel natural.

Do not introduce WebSockets unless a real requirement makes them necessary.

---

# 17. External Services

Keep external dependencies minimal.

Potential external service:

- LLM API provider.

The LLM provider should be accessed through a small abstraction so the application does not tightly couple business logic to a specific provider.

Do not introduce multiple LLM providers unless required.

---

# 18. Deployment

Prototype deployment should be simple.

Preferred structure:

```text id="qv44d7"
Frontend
   ↓
Backend
   ↓
PostgreSQL
   ↓
LLM API
```

Docker can be used for consistent local development and deployment.

No Kubernetes or complex cloud architecture is required.

---

# 19. Error Handling

The backend should gracefully handle:

- LLM failures.
- Invalid structured LLM output.
- Database errors.
- Missing profile information.
- Empty recommendation results.
- Invalid user input.

The user should receive a useful conversational response rather than a raw stack trace.

---

# 20. Architecture Constraints

Claude Code must **not**:

- Turn the application into microservices.
- Introduce Kubernetes.
- Introduce event-driven infrastructure without a real requirement.
- Create an agent swarm.
- Add multiple databases unnecessarily.
- Add Redis without a concrete need.
- Add a vector database without a concrete need.
- Create excessive abstraction layers.
- Optimize for thousands/millions of users.
- Build infrastructure that is not needed for the prototype.

### Preferred approach

```text id="r2s5t4"
Simple
   >
Correct
   >
Understandable
   >
Maintainable
   >
Highly optimized
```

For this project, **simplicity is a feature**.

---

# 21. Architecture North Star

The complete system should remain understandable as:

```text id="j6p7t2"
                    USER
                     │
                     ▼
              React Conversation
                     │
                     ▼
                  FastAPI
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
      LLM / Chat          Recommendation
                              Engine
          │                     │
          └──────────┬──────────┘
                     ▼
                 PostgreSQL
                     │
              ┌──────┴──────┐
              ▼             ▼
          Careers        Courses
           + Skills      + Skills
```

If a proposed architectural change makes the system significantly more complicated without improving the user experience, recommendation quality, reliability, or hackathon evaluation, **do not make the change**.