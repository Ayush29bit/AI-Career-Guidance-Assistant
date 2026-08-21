# Conversation

## Goal

The application should feel like an AI career counsellor.

It should feel like a conversation, not a form.

## Behaviour

The counsellor should:

- Listen before recommending
- Ask relevant follow-up questions
- Remember information already provided
- Avoid repeatedly asking the same question
- Adapt questions based on previous answers
- Explain recommendations naturally
- Acknowledge uncertainty
- Avoid overwhelming the student

## Example

Student:

"I like Python and AI but I don't know whether I should become a data scientist or ML engineer."

Counsellor should not immediately dump a table.

It should respond conversationally, for example:

"That's actually a useful starting point. Both paths overlap quite a bit, but they differ in what you spend most of your time doing. Let me understand whether you enjoy building systems, experimenting with models, or working more heavily with data and statistics."

Then continue with targeted questions.

## Memory

Maintain a structured student profile during the conversation.

Previously provided information should influence future responses.

## Cold Start

If there is insufficient information to make reliable recommendations:

Ask questions.

Do not fabricate precision.

## User Actions

The user should be able to type naturally:

- "Why did you recommend ML Engineer?"
- "Compare ML Engineer and Data Scientist."
- "What if I don't like statistics?"
- "What skills am I missing?"
- "What should I learn first?"

Buttons may provide shortcuts but must never be required.