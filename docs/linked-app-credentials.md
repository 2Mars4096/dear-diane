# Linked-app credentials

Todo uses Diane's existing file-backed provider key first, then its own key. It does not change either source. Models remain app-specific. Disable **Use Diane credentials when available** in Todo Settings to use its own key exclusively.

- Works for matching standard OpenRouter, OpenAI and Moonshot endpoints on the same computer. Custom endpoints never receive Diane credentials.
- Launch an updated Diane desktop once to publish `~/.dan/linked-credentials.json`. This private, atomic JSON file contains `schema_version: 1`, the absolute `saved_keys_file` path and an optional absolute `env_file` path. It contains no keys.
- Older installations can use `~/.dan/graphs_dir`; Todo reads `model_credentials/keys.json` beneath that directory and `.env` from its parent.
- Resolution mirrors Diane's file-backed provider precedence: saved Settings key; `DAN_<PROVIDER>_API_KEY`; `<PROVIDER>_API_KEY`; `KIMI_API_KEY` for Moonshot; generic `DAN_LLM_API_KEY`/`OPENAI_API_KEY` only when `DAN_LLM_BASE_URL` or `DAN_BASE_URL` matches that provider.
- Todo re-reads credentials for each new request. Missing, invalid or unreadable Diane files use Todo's fallback. A provider authentication failure is not retried using another account.
- Only availability is returned to the frontend. Native provider calls resolve the secret. Existing `.env` files and Settings key files remain unchanged.
- No Keychain integration, credential replication, cloud login, cross-device sync or requirement to keep Diane running. Credentials present only in Diane's process environment are unavailable to closed-app lookup.
