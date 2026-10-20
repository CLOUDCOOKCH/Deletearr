# Contributing

Read the architecture, safety ADRs, integration contracts and validation notes first. Use English for code and technical documentation. Keep changes scoped and preserve safe refusal behavior.

Python 3.12+ and Node 24 are required. Install the checked-in development and npm lockfiles. Run `scripts/check.sh`; run dependency audits after changing locks. Browser tests need Linux Chromium and a disposable backend/config directory. Never use production credentials or media in tests.

Add a behavioral regression test for a safety change. Test both allowed and refused execution, and inspect actual filesystem outcomes. Use `httpx.MockTransport` for API contracts; never replace a live destructive operation with an unconditional success response in production code. Any new manager mutation needs current supported documentation, persisted preconditions/checkpoints and verified postconditions.

SQLite migrations are additive and versioned. Do not weaken default dry-run, change existing live enrollment during migrations, infer never-watched from missing history, expand episode scope, or replace exact file operations with recursive directory deletion.

Do not include API keys, account tokens, real media paths or user watch history in issues, fixtures or screenshots. Use synthetic media and scrubbed contracts. Publishing a container/release is a maintainer action after release checks, not a normal test step.
