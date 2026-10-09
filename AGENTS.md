# Project guidance

Use the installed TypeSafe skill at `.agents/skills/typesafe-ai/SKILL.md` when working on this project. Read its live documentation before changing TypeSafe API contracts or question design. The skill and `skills-lock.json` were installed using `npx skills add typesafe-ai/skills --skill typesafe-ai --agent codex --yes`.

TypeSafe provides optional text judgments on full Scan. Preserve the distinction between reported appearance, camera pixels, and confirmed identity. Keep credentials server-side. Raw probabilities are advisory; do not introduce automatic tracking, handoff, or risk thresholds without evaluating representative labeled camera data. Missing credentials and failed or malformed reviews must remain explicitly unavailable, never successful reviews.

Validate behavior changes with `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v` and frontend changes with `npm run build --prefix frontend`. Live model accuracy requires separate observed-frame evaluation; mocked API tests establish contract handling, not accuracy.
