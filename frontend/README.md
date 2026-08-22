# Frontend

The counsellor interface: React + Vite + Tailwind, one screen, no router and no
state library. All of the app's state lives in `useConversation`, because there
is one conversation owned by one screen.

## Running it

The backend must be running first — the frontend has no mock mode and no
fixtures, by design.

```bash
cd backend
uvicorn app.main:app --port 8000

# in another terminal
cd frontend
npm install
npm run dev            # http://localhost:5173
```

`VITE_API_URL` points at the backend and defaults to `http://localhost:8000`.
Copy `.env.example` to `.env` to change it.

### If port 5173 is taken

The dev server uses `strictPort`, so it fails rather than quietly moving to
5174 — the backend's CORS list names the origin, and a silently different port
would produce blocked requests that look like backend failures. To run
elsewhere, set both sides:

```bash
# backend
CORS_ORIGINS=http://localhost:5175 uvicorn app.main:app --port 8000
# frontend
npm run dev -- --port 5175
```

## Structure

```text
src/
  api/         client.ts (the only fetch) + conversation.ts (one function per endpoint)
  components/
    Chat/                    the conversation surface
    ProfilePanel/            what the counsellor knows so far
    CareerRecommendations/   the engine's ranking
    SkillGaps/               top gaps for the strongest match
    Courses/  Roadmap/       built, not yet reachable (see below)
    ui/                      the four primitives the panel is made of
  hooks/       useConversation.ts -- all app state
  lib/         labels.ts -- display names for backend vocabularies
  pages/       CounsellorPage.tsx -- the one screen
  types/       api.ts -- the backend's response shapes
```

## The rule this frontend follows

**Every number on screen was computed by the backend.** Match scores, skill
gaps, priorities and proficiencies are rendered exactly as they arrive. The
frontend chooses widths, colours and wording; it never derives a value. If a
score looks wrong, the answer is in `backend/app/recommendation/`, and there is
nowhere else it could have come from.

The same rule covers absence: a section with no data renders an empty state
rather than a plausible-looking default. `experience_level: null` means unknown,
and unknown is shown as unknown.

## Courses and Roadmap

`Courses/CourseCard.tsx` and `Roadmap/RoadmapSteps.tsx` are written against the
engine's real shapes and are not currently reachable: the recommendation engine
builds both and the conversation layer already hands them to the LLM, but no
endpoint returns them yet. They render when an endpoint does. Nothing in the app
constructs a course, so there is no placeholder data to mistake for real data.
