# ResolveFlow AI - C08 Verification Report

**Phase:** C08 - Unified operations workspace

**Result:** GREEN

**Date:** 16 September 2026

## Scope and safety

No cloud resource was created, no real mailbox was connected, no request reached Google, and no paid model was called. Browser verification ran against a local dev server and a throwaway PostgreSQL 17 in the session scratchpad, seeded with synthetic conversations (`acme.example`, `customer@example.net`). Both were stopped and removed afterwards.

Docker was still unavailable, so the migration chain was again applied with exactly three pgvector statements excluded, each asserted to occur once. C08 adds no migration.

## Delivered

- **Operations overview** - counts by state, service-level risk, and a glossary explaining what each state means and why the provider's own unread flag is not used.
- **Unified inbox** - seven saved views, URL-backed filters (mailbox, queue, assignee, search, sort, page), bulk assignment, keyboard navigation, and an empty state that explains missing access.
- **Ticket workspace** - conversation, customer context, triage decision with rule codes explained in plain words, draft with grounding status and citations, action history, and the pipeline trace.
- **`lib/workspace/`** - `filters.ts` (URL query and saved views), `state.ts` (state, service level, relative time), `repository.ts` (SQL scoped by organization, mailbox permission, and department).
- **Accessibility as enforced rules** - a contrast-checked token palette, one focus treatment, a skip link, minimum tap targets, and a phone layout.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | 505 passed (unchanged; C08 adds no Python) |
| Web suite | **126 passed** (was 77 at C07) |
| Workspace logic and structure | 38 tests |
| Contrast, focus, and responsive rules | 11 tests |
| TypeScript, ESLint, Next.js build | clean; 3 new routes |
| Infrastructure checks | 0 findings |

## Browser, keyboard, contrast, and responsive evidence

Driven with Playwright against the dev server, signed in as two different people by seeding session rows.

**Structure and keyboard**

| Check | Result |
|---|---|
| First tab stop | the skip link, with a 3px visible outline that moves into view on focus |
| Landmarks and headings | banner, navigation, main; H1 followed only by H2s |
| `j`/`k` and arrows | move focus between conversation rows |
| Typing `j` in the search field | types the character and keeps focus - the shortcut stands aside |

**Authorization, seen through the interface**

| Check | Result |
|---|---|
| Dana (Admin) | sees 7 open conversations across both mailboxes |
| Sam (Agent, permission on one mailbox) | sees 5, all `support@acme.example` |
| The billing mailbox, for Sam | absent from the rows **and** from the mailbox filter |
| Sam opening a billing ticket by URL | **404**, identical to a ticket that does not exist |
| Bulk assignment controls, for Sam | not rendered; his role has no `ticket.assign` |

**Per-person read state**

| Check | Result |
|---|---|
| Conversations Dana had read | show as "Open" for Dana, "New to you" for Sam |
| Sam's "New to me" before opening #3 | 5 |
| After Sam opened #3 | 4, and #3 no longer listed |

**Service levels and states**, sorted soonest-target-first: breached, at risk, within SLA, then no target. A conversation with no policy reads "No SLA target" rather than being assumed on time. A pipeline failure (`MCP_UNAVAILABLE`) shows "Needs a person" rather than an unread badge.

**Contrast, computed in the browser**

| Element | Ratio |
|---|---|
| State badge | 7.30:1 |
| Service-level badge | 7.30:1 |
| Conversation link | 5.99:1 |
| Table header | 7.35:1 |
| Primary button | 6.70:1 |

All above the 4.5:1 AA minimum; the lowest measured was 5.99:1.

**Responsive at 390x844**: no horizontal overflow, no element wider than the viewport, the seven-column table collapses to stacked cards, and no interactive target below 24px.

**Console**: zero application errors. Two 404s appeared - the deliberate authorization test, and a missing favicon, now fixed with an app icon.

## Defects the verification caught

1. **Service-level "at risk" was nonsense.** The comment described a proportional window; the expression reduced to a flat fifteen minutes via a `remaining * 0` term that always evaluated to zero. Replaced with an explicit warning horizon and covered by tests. It would have quietly mis-warned on every queue whose target was not an hour.
2. **A border at 2.6:1.** Below the 3:1 minimum for meaningful boundaries, and entirely plausible-looking by eye. Caught by the stylesheet contrast test, not by review.
3. **Tap targets at 20px.** Below WCAG 2.2's 24px minimum. Caught by measuring the rendered page in a phone viewport - reading the stylesheet would not have revealed it, because no rule was wrong; a rule was missing.
4. **`Date.now()` during render.** React's purity rule flagged it. The fix is better than the original: one timestamp taken with the data, so two rows of equal age cannot render as different ages.
5. **`revalidatePath` during render** would have thrown when opening any conversation. Seen-marking moved off the server-action path to the repository.
6. **An agent with mailbox access saw an empty queue and no explanation.** Mailbox permission without a department assignment hides everything the department routes. Found only by driving the interface as a second, less-privileged person. Fixed with an empty state that says which access is missing - and it exposed that the "sees every department" role set had been written three times; it is now defined once.
7. **The framework wrote `AGENTS.md` and `CLAUDE.md` into the repository** when the dev server started. Disabled.
8. **Two stale test expectations** from my own refactors, both asserting a literal where the intent was broader.

## What C08 deliberately did not do

- **No ticket actions.** Approve, reject, reroute, and provider drafts are C12. The draft panel says so and states that nothing is ever sent.
- **No quality workspace.** Measuring accuracy, retrieval, and grounding against stored knowledge is C11.
- **No administration screens.** Mailbox connection, member and role management, and audit browsing have their permissions enforced but no surfaces yet.
- **No per-user saved views.** The seven views are fixed; naming and storing personal views is worth doing only once the pilot shows which ones people actually want.
- **No push updates.** Polling, pausable, per C-D087.
- **No provider unread.** Gmail's shared label cannot answer a per-person question, and the overview explains this rather than showing a number that would be wrong.

## Residual risks

| Risk | Treatment |
|---|---|
| Browser evidence was gathered interactively, not as a committed suite | The offline tests cover logic, contrast, focus, and structure on every commit. A Playwright suite in CI is worth adding at C14 when there is a staging URL to run it against |
| Tested with 8 conversations; list performance at thousands is unmeasured | Indexes exist from C04; measure with realistic volume during the pilot |
| Triage fields are empty until C11, so the decision and draft panels mostly show empty states | Intentional, and the empty states say why rather than looking broken |
| Screen-reader behaviour was verified structurally, not with an actual reader | Landmarks, headings, captions, labels, and live regions are in place; a screen-reader pass belongs in C14 acceptance |
| Assignment has no optimistic-locking conflict surface yet | The version column is bumped; showing a conflict to the person belongs with C12's review actions |
| The seeded demo used a session row written directly to the database | A test affordance only. Real sign-in is unchanged and still requires Google Workspace |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. The MVP core and its tests are unmodified. The dev server, database, and browser were stopped; the scratchpad database, browser artifacts, and framework-generated files were removed.

## Gate conclusion

C08 meets its gate: the critical journeys pass browser, keyboard, contrast, and responsive checks, and authorization holds when exercised through the interface as two different people. C09 may begin.
