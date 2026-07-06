# CLAUDE.md — project guidance for the `cante` repo

## GitHub: all pushes go through PR

This is an open-source project I maintain alone. **Never push directly to `main`.**

Every change destined for GitHub **must** be opened as a Pull Request, so that I (the
owner) always go to GitHub and explicitly accept/merge it. Concretely:

- **Branch first.** If work would land on `main`, create a descriptive branch
  (e.g. `fix/evolution-qr-connect-welcome`, `feat/...`) before committing.
- **Push a branch, then open a PR with `gh pr create`.** Do not push commits
  straight to `main` and consider the work done.
- **Title:** solid and specific — a concise imperative summary of the change
  (e.g. `fix(evolution): treat Baileys 'open' as connected; fix QR pairing + outbound JIDs`).
- **Description:** a clear account of what was done and why — root cause(s),
  what changed, anything propagated from a fork, tests added, and what was
  intentionally left out. Don't make me read the diff to understand it.
- **Language: English only** — titles, descriptions, and PR body are all in
  English, never Portuguese.

This rule lives only in the `cante` repo. Work in the `cante-cds` deployment
fork (and other projects) follows whatever rules apply there.
