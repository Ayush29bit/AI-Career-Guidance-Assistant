# AI Career Counsellor — Development Guide

## 1. Development Philosophy

This is a hackathon prototype.

Prioritize:

- Working features
- Clean, readable code
- Good UX
- Reliable recommendation logic
- Fast iteration

Prefer simple solutions over sophisticated architecture.

> If a simpler implementation solves the problem correctly, use it.

---

## 2. Architecture Rules

Use the agreed architecture:

```text
React
  ↓
FastAPI
  ↓
PostgreSQL
  ↓
LLM API
```

Keep the backend as a modular monolith.

Do **not** introduce the following without an explicit reason:

- Microservices
- Kubernetes
- Message queues
- Event-driven architecture
- Multiple databases
- Redis
- Separate vector databases
- Multi-agent systems
- Complex workflow engines

New infrastructure must solve an actual product requirement.

### Code Organization

Prefer a small number of meaningful modules.

Example:

```text
backend/
└── app/
    ├── api/
    ├── conversation/
    ├── recommendation/
    ├── llm/
    ├── database/
    └── main.py
```

Do not create:

- A class for every small function
- Unnecessary interfaces
- Deep abstraction layers
- Generic frameworks inside the project

Keep code easy to trace.

### Implementation Order

Build incrementally.

Recommended order:

1. Project setup
2. Database and models
3. Career and skill data
4. Coursera data ingestion
5. Basic recommendation engine
6. Conversation and profile extraction
7. Skill-gap analysis
8. Course recommendations
9. Roadmap
10. Career comparison
11. What-if analysis
12. UI polish
13. Testing
14. Deployment

Do not build Tier 2 features before the core Tier 1 flow works.

### Backend Rules

Use:

- FastAPI
- Pydantic
- SQLAlchemy
- PostgreSQL

Keep business logic separate from API route handlers.

Routes should primarily:

```text
Validate request
      ↓
Call application logic
      ↓
Return response
```

Do not put large recommendation algorithms directly inside API endpoints.

### LLM Rules

The LLM should handle:

- Conversation
- Profile extraction
- Intent understanding
- Follow-up questions
- Explanations

Normal Python logic should handle:

- Career scoring
- Skill gaps
- Course ranking
- Sorting
- Database operations

Do not use an LLM when deterministic code is sufficient.

Do not hardcode business logic into prompts when it belongs in Python.

### Database Rules

Use PostgreSQL as the primary database.

Database changes should be reproducible through migrations. Do not manually modify production or schema state without recording the change.

Keep queries understandable. Do not optimize queries prematurely; only add indexes when there is a clear reason.

### API Rules

Use consistent REST-style endpoints.

```text
/api/v1/conversations
/api/v1/conversations/{id}/messages
/api/v1/profile
/api/v1/recommendations
/api/v1/careers/{id}
/api/v1/careers/compare
/api/v1/recommendations/what-if
/api/v1/skill-gaps/{career_id}
/api/v1/courses/recommended
/api/v1/roadmap/{career_id}
```

Keep request and response schemas explicit with Pydantic. Do not expose internal implementation details in API responses.

### Error Handling

Handle expected failures gracefully:

- LLM failure
- Invalid LLM output
- Database failure
- Invalid input
- Missing data
- No recommendations found

Users should receive useful messages.

Never expose stack traces, API keys, database errors, or internal prompts to the frontend.

### Environment Variables

Secrets must never be committed.

Use `.env` and `.env.example`. Add `.env` to `.gitignore`.

Example:

```dotenv
DATABASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

Never hardcode API keys or credentials.

### Testing

Focus testing on important business logic.

At minimum, test:

**Recommendation logic**

- Career scoring
- Career ranking
- Skill matching
- Skill gaps
- Course ranking
- What-if recalculation

**Backend**

- Important API endpoints
- Invalid input
- Basic error handling

**LLM**

- Structured extraction with representative conversations

Do not chase arbitrary high test coverage. Test the logic that can actually break the product.

### Git Rules

Git identity must remain the developer or team's identity.

Claude Code must **not**:

- Change Git user configuration without explicit instruction
- Set itself as the Git author
- Add itself as a contributor
- Add Anthropic as a contributor
- Add Claude attribution to commits

Do **not** add the following, or equivalent attribution, unless explicitly requested:

```text
Co-authored-by: Claude
Co-authored-by: Anthropic
```

Claude Code should not modify GitHub account configuration.

### Commits

Use clear, human-readable commit messages.

Examples:

```text
feat: add career recommendation engine
feat: add conversational profile extraction
fix: handle invalid LLM responses
feat: add skill gap analysis
```

Do not create meaningless commits such as:

```text
AI generated changes
Claude update
misc fixes
```

Keep commits focused where practical.

### Git Safety

Before modifying Git configuration, remotes, authentication, or repository identity, stop and ask for explicit confirmation.

Do not do the following unless explicitly instructed:

- Change `user.name`
- Change `user.email`
- Change remotes
- Create accounts
- Authenticate GitHub
- Push to a remote

Normal code changes can proceed without asking.

### Dependencies

Only add a dependency when it provides clear value.

Before adding a library, ask: can the existing stack solve this cleanly?

Avoid dependency sprawl. Do not add frameworks simply because they are popular in AI projects.

### Documentation

Update relevant documentation when an architectural or product decision changes.

Important project docs:

```text
docs/
├── PRODUCT.md
├── CONVERSATION.md
├── ARCHITECTURE.md
├── DATA.md
├── RECOMMENDATIONS.md
├── AI.md
├── UI.md
└── DEVELOPMENT.md
```

Do not create new documentation files unless they provide meaningful value.

### Scope Control

The current scope is Tier 1 and Tier 2.

Do not independently add:

- Voice
- Authentication systems
- Admin dashboards
- Progress tracking
- Notifications
- Mobile apps
- Enterprise functionality
- Advanced analytics

If a potentially useful feature is outside the scope, mention it rather than implementing it.

### Definition of Done

Before considering a feature complete:

- It works with realistic input.
- It integrates with the existing architecture.
- Important logic has basic tests.
- Errors are handled reasonably.
- No secrets are committed.
- Existing functionality still works.
- The implementation is understandable.
- No unnecessary infrastructure was introduced.

### Claude Code Working Rule

Before implementing a significant feature:

1. Read the relevant project documentation.
2. Understand the existing implementation.
3. Reuse existing code where appropriate.
4. Make the smallest clean change that solves the problem.
5. Test the change.
6. Report what changed and any important limitations.

Do not rewrite working code simply to match a preferred coding style. Do not refactor unrelated parts of the project while implementing a feature.

### Final Principle

This project should be impressive because of its:

- Product experience
- Conversational AI
- Recommendation quality
- Personalization
- Explainability
- Useful features
- Demo execution

It should **not** try to be impressive because it has a complicated architecture.

Build a small system that does something meaningful really well.
