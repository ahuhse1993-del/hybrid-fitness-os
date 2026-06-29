# CAIRN Coach Standard

## Document 12 – API Architecture

**Version:** 1.0
**Status:** Production Ready

---

# Purpose

The API Architecture defines how information flows through CAIRN.

It does not define programming languages.

It does not define cloud providers.

It does not define implementation details.

It defines the architecture of coaching.

Every technology choice should support this architecture.

Never replace it.

---

# Core Principle

CAIRN is a coaching system.

Artificial intelligence is one component.

Not the system itself.

Data exists to support coaching.

Models exist to apply coaching.

The athlete experiences coaching.

Never infrastructure.

---

# Architectural Philosophy

Every request follows the same journey.

Data

↓

Context

↓

Reasoning

↓

Decision

↓

Communication

The athlete should never receive raw data.

Only coached information.

---

# Separation Of Responsibilities

Each system component has exactly one responsibility.

Data collection should never make coaching decisions.

Reasoning should never collect data.

Communication should never invent reasoning.

Clear separation creates reliable coaching.

---

# Model Independence

Every language model should behave identically.

Changing from OpenAI to Anthropic,

Gemini,

or any future provider

must never change the coaching philosophy.

Models are interchangeable.

The Coach Standard is not.

---

# Stateless Intelligence

Language models should remain stateless.

Long-term coaching memory belongs to CAIRN.

Not the language model.

Every request receives only the context required for today's decision.

The model reasons.

CAIRN remembers.

---

# The Athlete Journey

Every interaction follows the same architecture.

Athlete Request

↓

Relevant Data

↓

Coach Memory

↓

Coach Standard

↓

Reasoning

↓

Decision

↓

Output Format

↓

Athlete

Every layer exists to improve coaching quality.
# System Architecture

CAIRN is composed of independent systems.

Each system has one responsibility.

Together they create one coach.

No individual component should attempt to replace another.

---

# Layer 1 — Data Layer

Purpose

Collect information.

Never interpret it.

Examples

Garmin.

COROS.

Suunto.

TrainingPeaks.

Intervals.icu.

Apple Health.

Manual Check-in.

Coach Notes.

Environmental Data.

Calendar.

The Data Layer provides observations.

Nothing more.

---

# Layer 2 — Knowledge Layer

Purpose

Provide coaching knowledge.

Components include:

Identity.

Communication.

Decision Engine.

Recovery Framework.

Training Engine.

Strength Framework.

Nutrition Framework.

Race Framework.

Planning Engine.

Adaptation Engine.

Knowledge Rules.

The Knowledge Layer defines how coaching should happen.

It never changes because of today's athlete.

---

# Layer 3 — Athlete Layer

Purpose

Represent the individual athlete.

Contains

Athlete Profile.

Coach Memory.

Training History.

Recovery History.

Preferences.

Goals.

Injury History.

Personal Context.

This layer personalises coaching.

Without changing coaching philosophy.

---

# Layer 4 — Reasoning Layer

Purpose

Transform information into decisions.

The Reasoning Layer combines:

Current data.

Coach Memory.

Knowledge Layer.

Current context.

Long-term goals.

The result is one coaching decision.

Not one statistical calculation.

---

# Layer 5 — Decision Layer

Purpose

Choose the best coaching action.

Possible outcomes include:

No change.

Workout adjustment.

Plan adaptation.

Recovery recommendation.

Strength recommendation.

Nutrition recommendation.

Race strategy.

Educational explanation.

Every decision should be explainable.

If it cannot be explained,

it should not be delivered.

---

# Layer 6 — Communication Layer

Purpose

Transform decisions into coaching conversations.

Uses:

Communication Standard.

Output Formats.

Athlete Personality.

Current emotional context.

The athlete never sees internal reasoning.

The athlete experiences coaching.

---

# Layer 7 — Learning Layer

Purpose

Improve future coaching.

After every interaction,

CAIRN evaluates:

What happened?

What worked?

What surprised us?

Should Coach Memory change?

Should confidence increase?

Should future planning change?

Learning never ends.

Neither does coaching.

---

# Final Principle

The architecture should allow every individual layer to improve independently.

New sensors.

New AI models.

New research.

New output formats.

None of these should require changing the coaching philosophy.

That separation protects CAIRN's identity.

---

# System Rule

Technology serves coaching.

Coaching never serves technology.

Every architectural decision should preserve this principle.

That is the foundation of CAIRN.
# Coaching Flow

Every coaching interaction follows the same architecture.

The athlete should experience one continuous conversation,

even though multiple systems collaborate internally.

---

# Step 1 — Observe

The system gathers observations.

Examples

New activity.

Morning recovery.

Heart rate.

HRV.

Sleep.

Training history.

Coach notes.

Weather.

Calendar.

Manual check-in.

The objective is collecting reality.

Not interpreting it.

---

# Step 2 — Build Context

The system combines today's observations with long-term knowledge.

Context includes:

Athlete Profile.

Coach Memory.

Current Training Block.

Long-Term Goal.

Recovery Status.

Previous Decisions.

Recent Adaptations.

Today's observations now become meaningful.

---

# Step 3 — Think

The Decision Engine evaluates the situation.

Questions include:

What happened?

Why did it happen?

What matters?

What does not matter?

What should happen next?

No recommendation exists before reasoning.

---

# Step 4 — Decide

The system selects one coaching decision.

Examples

Continue as planned.

Reduce intensity.

Increase recovery.

Adapt the week.

Explain a trend.

Teach a concept.

Celebrate progress.

Challenge behaviour.

Every response begins with one clear decision.

Everything else supports it.

---

# Step 5 — Communicate

The decision is transformed into coaching.

The Communication Standard determines:

Tone.

Language.

Humour.

Directness.

Empathy.

Output Formats determine the structure.

The athlete experiences one coach.

Not multiple systems.

---

# Step 6 — Learn

After every interaction,

CAIRN evaluates whether something should be remembered.

Questions include:

Did we learn something about the athlete?

Should Coach Memory change?

Did today's decision work?

Should future planning change?

Learning transforms today's interaction into tomorrow's advantage.

---

# Continuous Coaching

CAIRN never treats interactions as isolated events.

Every conversation influences future coaching.

Every race improves future planning.

Every workout improves future interpretation.

Every setback improves future decisions.

The coach evolves with the athlete.

---

# Failure Handling

If information is incomplete,

CAIRN should reduce confidence.

Not invent certainty.

If uncertainty becomes meaningful,

CAIRN asks questions before making major decisions.

Curiosity is preferable to assumption.

---

# Explainability

Every recommendation should be explainable.

Internally,

the system should always know why a recommendation exists.

Externally,

the athlete should receive an explanation appropriate to the situation.

Opaque coaching is not trusted coaching.

---

# Scalability

The architecture should remain stable as CAIRN grows.

New sports.

New devices.

New sensors.

New AI models.

New coaching modules.

All should integrate without changing the coaching philosophy.

Growth should add capability.

Never complexity.

---

# The CAIRN Principle

The athlete should never wonder:

"Which model answered me?"

The athlete should only ever think:

"My coach knows me."

Everything inside the architecture exists to make that sentence true.

---

# Version 1.0

This repository does not define an AI assistant.

It defines a professional coaching system.

Artificial intelligence provides reasoning.

Data provides evidence.

Technology provides infrastructure.

The Coach Standard provides identity.

Identity creates trust.

Trust creates consistency.

Consistency creates performance.

That is CAIRN.

---

# Final Statement

The purpose of CAIRN is not replacing human coaches.

The purpose is making world-class coaching available every day,

to every athlete,

at exactly the moment they need it most.

Technology made this possible.

Coaching makes it meaningful.

---

# End of Coach Standard

Coach well.

Everything else follows.