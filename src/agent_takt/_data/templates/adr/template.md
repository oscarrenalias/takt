---
id: {{id}}
title: {{title}}
status: draft
created_at: {{created_at}}
authors: {{authors}}
description: {{description}}
tags: {{tags}}
related_specs: {{related_specs}}
related_beads: {{related_beads}}
supersedes: {{supersedes}}
---
# {{title}}

## Summary

> In the context of <use case>, facing <concern>, we decided for <option>, to achieve <quality>, accepting <downside>.

One sentence in the structured form above. This is the executive line — operators and agents read it to grasp the decision without parsing the whole document.

## Context

What is the issue, situation, or pressure motivating this decision? Be concrete — name the constraints, the stakeholders, the prior art, and any failed prior attempts. Future agents will read this section to understand whether the decision still applies to their situation.

## Decision Drivers

*Optional but strongly recommended.* Specific forces pushing this decision — constraints, quality attributes, or non-negotiable requirements. Each driver is a one-line bullet.

* (driver 1)
* (driver 2)

## Considered Options

The options that were on the table. Each option gets its own subsection with bullet lists of arguments for and against. This is the section that prevents future agents from re-proposing rejected alternatives — keep the "Bad" bullets specific enough to actually argue against, not just hand-waved.

### Option A — (name)

* Good: …
* Good: …
* Bad: …

### Option B — (name)

* Good: …
* Bad: …
* Bad: …

## Decision

The chosen option, stated directly and unambiguously. If the decision has a binding implication ("X is forbidden", "Y is the default", "all Z must be Q"), state it as such. This is the section that future agents will treat as a constraint — keep it crisp.

## Consequences

### Positive

* (what becomes easier, safer, or otherwise improved)
* …

### Negative

* (what we accept as the cost of this decision)
* …
