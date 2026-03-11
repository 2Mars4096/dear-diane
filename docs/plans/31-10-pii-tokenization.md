# 31-10: PII Tokenization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Prevent sensitive personal data (names, passwords, addresses, phone numbers) from being sent to external LLM APIs by replacing them with semantic placeholders before API calls and restoring them in responses.

## Problem

Every LLM API call sends the full prompt — including any user data — to an external server. For personal and professional use, this means real names, addresses, passwords, financial data, and other PII may be transmitted. Users need a way to protect sensitive information without losing conversational coherence.

## Tasks

- [x] 1. **Sensitive word registry**
  - [x] 1-1. `SensitiveWordRegistry` — loads user-defined sensitive words from `~/.dan/sensitive_words.json`: `{"words": [{"value": "John Smith", "category": "name"}, {"value": "123 Main St", "category": "address"}, ...]}` 
  - [x] 1-2. Auto-detection patterns: regex-based detection of common PII formats (email addresses, phone numbers, SSNs, credit card numbers, IP addresses) — runs in addition to user-defined words
  - [x] 1-3. Category taxonomy: `name`, `address`, `email`, `phone`, `password`, `financial`, `id_number`, `custom`
  - [x] 1-4. `/pii add "John Smith" --category name` and `/pii list` and `/pii remove "John Smith"` chat commands
  - [x] 1-5. `DAN_PII_PROTECTION=1` env var to enable (default off — opt-in for privacy-conscious users)

- [x] 2. **Tokenizer (outbound)**
  - [x] 2-1. `PIITokenizer` — scans outgoing LLM prompts for registered sensitive words and detected PII patterns
  - [x] 2-2. Replacement strategy: semantic placeholders, NOT random hashes. `John Smith` → `[PERSON_1]`, `123 Main St` → `[ADDRESS_1]`, `password123` → `[PASSWORD_1]`. The LLM understands these as substitutions and reasons about them correctly.
  - [x] 2-3. Word-boundary-aware matching: "John" should match "John" but not "Johnson". Possessives ("John's") and contractions should tokenize to "[PERSON_1]'s" — preserve the grammatical suffix, replace only the PII. Use `\b` regex boundaries with suffix-aware capture groups.
  - [x] 2-4. Case-insensitive matching with original-case preservation in the mapping table
  - [x] 2-5. Deterministic mapping: same word always maps to the same placeholder within a session (maintain a `PIISession` mapping table)
  - [x] 2-6. Multi-word support: "John Smith" matched as a phrase before individual words; longest-match-first ordering

- [x] 3. **Detokenizer (inbound)**
  - [x] 3-1. `PIIDetokenizer` — scans LLM responses for placeholders and restores original values
  - [x] 3-2. Handle variations: `[PERSON_1]`, `PERSON_1`, `Person_1` — normalize before lookup
  - [x] 3-3. Leak detection: scan response for original sensitive words that weren't replaced (shouldn't happen, but defense-in-depth) — log warning if found
  - [x] 3-4. Context-aware restoration: if the LLM generates new text around a placeholder, ensure the restored text fits grammatically (heuristic — insert the original value in place of the placeholder token)

- [x] 4. **Integration hooks**
  - [x] 4-1. **Canonical owner:** tokenization lives at the **provider boundary** via a `TokenizingProviderWrapper` decorator that wraps any `LLMProvider`. This ensures both `LLMExecutor` (workflow) and `ChatManager` (chat) paths are covered, since `ChatManager` calls `provider.complete()` directly in ~8 places without going through `LLMExecutor`. The wrapper tokenizes all outbound message content and detokenizes inbound response text, using the `PIISession` attached to the call context.
  - [x] 4-2. `PIISession` accessible via `current_pii_session` `ContextVar` — `set_current_pii_session()` binds a session at the request boundary, `TokenizingProviderWrapper` resolves from the ContextVar when no explicit session is passed. Per-task isolation via standard `ContextVar` semantics.
  - [x] 4-3. Do **not** rewrite local tool-call arguments (file paths, IDs, URLs, search terms, code snippets). Tool inputs remain exact so local execution stays correct. If tool output is later assembled into an outbound LLM prompt, tokenize at that later outbound boundary.
  - [x] 4-4. Scope: only applies to external API calls — local processing, file I/O, and on-device operations are unaffected

- [x] 5. **Session management**
  - [x] 5-1. `PIISession` — per-conversation mapping table: `{placeholder: original_value}`. Persisted in memory only (never written to disk in plaintext).
  - [x] 5-2. Session lifecycle: created on first tokenization, cleared on conversation end or explicit `/pii clear-session`
  - [x] 5-3. Cross-turn consistency: the same word gets the same placeholder across all turns in a conversation

- [x] 6. **Tests and docs**
  - [x] 6-1. Unit tests: registry CRUD, tokenization (single word, multi-word, regex patterns, word boundaries, case sensitivity), detokenization, leak detection
  - [x] 6-2. Integration test: end-to-end message flow with PII — verify original words never appear in the outgoing API call
  - [x] 6-3. Edge cases: PII in code blocks (tokenized by default; skipped with `DAN_PII_SKIP_CODE_BLOCKS=1` via `_CODE_BLOCK_RE` splitting in `tokenize()`), PII in tool arguments (NOT tokenized — `_tokenize_messages` skips `tool_calls` field), PII in system prompts (tokenized — all message roles processed)
  - [x] 6-4. Update architecture, changelog, `.env.example`

## Dependencies

- `LLMExecutor` for outbound hook
- `ChatManager` for PIISession lookup / conversation scoping only
- `ProviderRegistry` — tokenization happens at the provider boundary
- `Concierge` for `/pii` command dispatch

## Estimate

2 days

## Notes

- **Semantic placeholders, not hashes.** `[PERSON_1]` works because LLMs understand it as a variable substitution. Random hashes like `a3f8b2c1` confuse the model and degrade response quality.
- **Opt-in by default.** PII protection adds latency (regex scanning) and may occasionally cause issues with code that contains matched patterns. Users who need it enable it explicitly.
- **Never persist the mapping to disk.** The `PIISession` mapping table exists only in memory. If someone compromises the disk, they can't recover the PII↔placeholder mappings.
- **Single ownership via wrapper.** `TokenizingProviderWrapper` decorates the provider so every outbound call is tokenized regardless of whether `LLMExecutor` or `ChatManager` initiated it. No caller rewrites content directly. Crash recovery: if the process crashes mid-conversation, the in-memory `PIISession` is lost. On restart, deterministic mapping from `SensitiveWordRegistry` can reconstruct the session for known words — but previously-seen LLM responses containing placeholders can't be retroactively detokenized. Document this limitation.
- **This is not a full DLP solution.** It's a pragmatic first step for personal use. Enterprise-grade DLP would require tokenization at the network layer, audit logging, and compliance features.
- **Code blocks:** By default, PII inside fenced code blocks is still tokenized (code often contains hardcoded credentials). Users can opt out with `DAN_PII_SKIP_CODE_BLOCKS=1` if tokenization breaks their code generation workflows.
