# Claude Code Quick Reference
# F1 Strategy Engine Project

## Session Slash Commands (type these inside claude)

| Command          | When to use                                      |
|------------------|--------------------------------------------------|
| /run-day         | Start of every daily build session               |
| /add-endpoint    | Adding a new API route (all 4 layers at once)    |
| /add-ml-model    | Adding a new ML model to services/ml/            |
| /migrate         | After changing any SQLAlchemy model              |
| /debug-test      | When make test fails and cause is not obvious    |
| /pre-commit      | Before every git commit                          |

## Built-in Claude Code Commands

| Command          | What it does                                     |
|------------------|--------------------------------------------------|
| /compact         | Compress conversation history (use at 70% ctx)  |
| /clear           | Fresh session (keeps CLAUDE.md, loses history)   |
| /context         | Show current context window usage %              |
| /rewind          | Undo last set of file changes                    |
| /model           | Switch between Sonnet and Opus                   |
| /doctor          | Diagnose Claude Code setup issues                |
| /voice           | Enable push-to-talk voice input (spacebar)       |

## Context Window Management Rules

- 0–50%   → Work freely
- 50–70%  → Wrap up current task, then /compact
- 70–90%  → /compact immediately
- 90%+    → /clear mandatory, start fresh

## Daily Ritual (do this every morning)

```
1. cd f1-strategy-engine
2. claude
3. /run-day
   [paste today's bullet points from the timeline doc]
4. Review Claude's plan → approve → watch it build
5. At end of session: /pre-commit → git commit → git push
6. Update "Current Project Phase" in CLAUDE.md
```

## When Claude Goes Wrong

- Wrong direction for > 5 minutes → /rewind then clarify your prompt
- Made a breaking change → git diff to see what changed, then fix or revert
- Tests failing after a session → /debug-test with the failing test path
- Context got too large → /compact, then re-anchor: "We are on Day X building Y"

## Anchor Prompt (use this to start every session)

Paste this at the start, then immediately invoke /run-day:

```
This is the F1 Strategy & Telemetry Engine project.
Read CLAUDE.md first to orient yourself on the full stack and conventions.
Today is Day [X]. We are in Phase [Y].
Last session we completed: [one sentence].
Today's goal: [one sentence].
No need to plan things on your own for day [X], I will provide the exact day [X] spec myself.
```

## File Locations

| File                              | Purpose                        |
|-----------------------------------|--------------------------------|
| CLAUDE.md                         | Project memory (always read)   |
| .claude/commands/run-day.md       | Daily session workflow         |
| .claude/commands/add-endpoint.md  | New endpoint scaffolding       |
| .claude/commands/migrate.md       | Safe migration workflow        |
| .claude/commands/debug-test.md    | Test debugging workflow        |
| .claude/commands/add-ml-model.md  | New ML model scaffolding       |
| .claude/commands/pre-commit.md    | Pre-commit quality gate        |
| .claude/settings.json             | Hooks (auto-lint, guards)      |
| ~/.claude/CLAUDE.md               | Global personal preferences    |