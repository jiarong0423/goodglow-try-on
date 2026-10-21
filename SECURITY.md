# Security

Please report a security problem privately through GitHub's "Report a vulnerability" (Security tab) instead of opening a public issue.

Notes for deployers:
- The public demo runs in `--mock` mode: no YouCam key is needed and none should be set.
- Live mode needs your own `YOUCAM_API_KEY` in the server environment (or an untracked `.env`); never commit it. Live mode has no user login, so keep it for local use and rely on `--max-calls` and `--budget-cap`.
