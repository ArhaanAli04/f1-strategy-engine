# /run-day — Start a day's build session

Use this command at the beginning of every daily work session.

## What to do

1. Read CLAUDE.md in full if you have not already done so this session.

2. Run these orientation commands before touching any code:
   - `git log --oneline -10` to see recent commits
   - `git status` to see any uncommitted changes
   - `find backend/ -name "*.py" | sort` to see current file inventory
   - `cat CLAUDE.md | grep -A 5 "Current Project Phase"` to read today's context

3. Ask the user: "Confirm today is Day [X] and the goal is [Y]. 
   Should I proceed with implementation or do you want to adjust anything first?"

4. Wait for confirmation before writing any file.

5. After confirmation: implement each bullet point from the day's timeline 
   one at a time. After each file is written, run the relevant check:
   - Python file → `cd backend && python -c "import <module>"` (syntax check)
   - Migration → `make migrate` and verify it passed
   - Service method → run the corresponding unit test if it exists
   - API endpoint → confirm it appears in `GET /docs`

6. After all files for the day are written:
   - Run `make lint` — fix all errors before proceeding
   - Run `make test-unit` — all must pass
   - Run `git add . && git commit -m "Day X: [summary]"`

7. Update the "Current Project Phase" block in CLAUDE.md with today's completed work.

## Usage
Paste the day's bullet points from the timeline doc after invoking this command.