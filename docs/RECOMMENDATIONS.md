# Recommendations

## Career Score

Career suitability:

- 50% Skill Match
- 25% Interest Match
- 15% Preference Match
- 10% Experience Fit

Final score is calculated by deterministic Python.

The LLM only explains the result.

## Skill Match

Compare student proficiency with the required career proficiency.

Missing skills contribute to the score.

Excess proficiency should not compensate for unrelated missing skills.

Career skill importance affects both scoring and gap priority.

## Interest Match

Compare student interests with controlled career interest tags.

## Preference Match

Compare student work preferences with career work-style tags.

## Experience Fit

Experience levels:

- Beginner
- Intermediate
- Advanced

Meeting or exceeding the expected level is full fit.

Unknown experience is neutral rather than assumed.

## Cold Start

Do not rank careers when the profile is too thin.

Current minimum:

- 3 skills
- 1 interest
- 1 work preference

Instead, continue the conversation and gather information.

## Skill Gaps

Gap:

required level − student level

Prioritize using:

gap × importance

Do not simply dump every missing skill.

## Course Ranking

Course score:

- 50% Skill Coverage
- 20% Difficulty Fit
- 20% Rating
- 10% Popularity

Course recommendations must be driven by prioritized skill gaps.

## Course Honesty

If there is no suitable Coursera course for a gap:

Tell the user.

Do not fabricate coverage.

## Roadmap

Build the learning sequence from:

1. Skill-gap priority
2. Skill prerequisites
3. Suitable courses
4. Course difficulty and duration

The roadmap should be personalized rather than a generic curriculum.

## Explainability

Every recommendation should be explainable through structured factors such as:

- Skill match
- Interest match
- Preference match
- Experience fit
- Missing skills
- Strengths
- Concerns