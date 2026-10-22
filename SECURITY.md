# Security

Please report a security problem privately through GitHub's "Report a vulnerability" (Security tab) instead of opening a public issue.

Notes for deployers:
- The public demo runs in `--mock` mode by default: no YouCam key is needed and anyone can use it without signing in.
- Live mode needs your own `YOUCAM_API_KEY` in the server environment (or an untracked `.env`); never commit it. Plain live mode (`python3 app/server.py`) has no user login, so keep it for local use and rely on `--max-calls` and `--budget-cap`.
- For a hosted site that should also allow real YouCam calls, use `--judge-live`: visitors stay in demo mode, and only a browser signed in at `/judge` uses live mode. Set `GOODGLOW_JUDGE_USER` and `GOODGLOW_JUDGE_PASSWORD` (at least 10 characters) in the platform's environment settings, never in the repository. The sign-in cookie is HttpOnly and signed with a key generated at each start; repeated wrong passwords lock sign-in for a minute.
- The container image switches to this mode only when `GOODGLOW_LIVE=1`, `YOUCAM_API_KEY`, `GOODGLOW_JUDGE_USER` and `GOODGLOW_JUDGE_PASSWORD` are all set; otherwise (including a password shorter than 10 characters) `app/serve.sh` logs which setting is missing or too short, never the value, and starts in demo mode. Mount a persistent volume at `/srv/app/runs` so the credit ledger survives redeploys.
