# Named feature case studies

These examples show the same blueprint process applied to different feature
shapes. Named uses Flutter, FastAPI, PydanticAI, MySQL, durable jobs, and a
replaceable model provider; copy the decision pattern, not the product details.

## Discover: synchronous AI + deterministic persistence

- **Shape:** app sends text or an image to a synchronous API. A Discovery agent
  returns one structured Word/Scene result.
- **Why AI:** language understanding, classification, and content generation are
  probabilistic.
- **Ownership:** the agent returns candidates and content; the service validates,
  caches WordContent, writes Discovery/Word records, and generates media.
- **Failure:** AI failure maps to a bounded API error; no partial fake content is
  persisted.
- **Lesson:** AI can be central without owning the database. The contract and
  persistence stay in the service.

## Talk: synchronous agent + context assembly

- **Shape:** save the learner message, assemble relevant context, call a Talk
  agent, persist the reply and learning signals, return immediately.
- **Why AI:** natural conversation, correction, annotation, and memory extraction.
- **Ownership:** the service owns conversation state, message IDs, idempotency,
  and persistence. The agent returns reply, corrections, annotations, and
  candidate memories.
- **Context:** Soul, memory, recurring errors, relevant experiences, and recent
  history are selected deliberately; not every source enters every turn.
- **Lesson:** context assembly is a service responsibility. The agent receives a
  bounded, explainable view.

## Experience: async extraction + evidence validation

- **Shape:** the Talk turn returns first. A durable job is debounced, then a worker
  calls the Experience agent.
- **Why async:** extraction is slow and must not delay the conversation.
- **Ownership:** the agent proposes at most one life and one learning experience;
  the service validates evidence against real messages and maps source IDs.
- **Failure:** worker failure does not fail Talk. Retries are bounded and jobs are
  durable.
- **Lesson:** when a feature has deferred side effects, design the job, retry,
  evidence, and failure isolation as first-class parts of the feature.

## Challenge: deterministic business logic, no agent

- **Shape:** create a challenge, select existing discoveries, track visits, end
  manually, and keep content after the challenge ends.
- **Why no AI:** ownership, state transitions, limits, and progress are rules.
- **Ownership:** services and domain code enforce one active challenge, item
  membership, and lifecycle; AI is not involved at runtime.
- **Lesson:** an AI-shaped product does not make every feature AI-shaped. Prefer
  deterministic code for rules and calculations.

## Intent router: small AI boundary before a larger flow

- **Shape:** composer text goes to a dedicated intent route before discovery. The
  agent returns `review_words`, `start_challenge`, `view_challenges`, or `none`.
  Actions short-circuit; `none` continues to discovery.
- **Why AI:** natural language and mixed Chinese/English cannot be classified
  reliably by client-side keywords.
- **Ownership:** the agent only returns an intent. The app performs navigation and
  reads current challenge state; discovery is not called for actions.
- **Failure:** the command path preserves input and surfaces an error; it does not
  create a misleading discovery.
- **Lesson:** a small, separate agent is appropriate when its contract is narrow
  and its decision changes the main flow. Do not fold semantic routing into UI
  parsing.

## Word lookup and Sentence: narrow contracts

- **Word lookup:** resolve selected text to one canonical English word; a second
  agent may generate content if it is not cached.
- **Sentence:** explain a selected phrase or sentence with natural English,
  chunks, structure, and key words.
- **Lesson:** split agents by output contract and consumer, not by model or
  endpoint count. Narrow agents are easier to fake, test, and replace.

## Pattern summary

| Feature shape | Core architecture |
| --- | --- |
| Deterministic rules | Service + domain + persistence; no agent |
| One probabilistic output on a request | Synchronous agent + service validation + persistence |
| Deferred interpretation | Synchronous primary path + durable job + worker + evidence validation |
| Small decision that changes navigation | Narrow intent agent before the main flow |
| Multiple related AI outputs | Separate narrow agents only when contracts and consumers differ |
