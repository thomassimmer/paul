# About

Two things that are not about using Paul: how it was built, and where the name comes
from.

## How this was built

Paul was written in a small number of concentrated sessions, mostly by directing an
AI coding agent (DeepSeek V4 Flash). It is worth saying plainly, because it changes how
the code should be read.

- **Directed, not generated on its own.** The architecture, the module boundaries,
  the data model and the security stance (untrusted offer text, no auto-apply,
  deterministic scoring) are deliberate choices, and each one is worth challenging in
  an issue.
- **Tests came with the code.** The suite is about as large as the application. That
  is what makes an AI-assisted codebase reviewable: it pins the behaviour that
  matters, and it is the first thing to run.
- **Human reviewed.** Every generated document passes through an editing loop, and
  the same goes for the code: the diff is read, the failing case is reproduced, and
  the behaviour is checked against the project's principles before it lands.

The point is not the tooling. It is that "an agent helped write it" and "it is
trustworthy" are not in tension, as long as someone can explain every decision in
it — which is the bar this repository holds itself to.

## The name

The name is a French pun. _Paul_ sounds like _Pôle_, so **Paul Emploi** is a
near-homophone of **Pôle Emploi**, the French public employment agency (now _France
Travail_).
