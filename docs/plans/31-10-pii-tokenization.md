# 31-10: PII Tokenization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Prevent sensitive personal data (names, passwords, addresses, phone numbers) from being sent to external LLM APIs by replacing them with semantic placeholders before API calls and restoring them in responses.

## Problem

Every LLM API call sends the full prompt — including any user data — to an external server. For personal and professional use, this means real names, addresses, passwords, financial data, and other PII may be transmitted. Users need a way to protect sensitive information without losing conversational coherence.

## Tasks

- [ ] 1. **Sensitive word registry**
  - [ ] 1-1. `SensitiveWordRegistry` — loads user-defined sensitive words from `~/.dan/sensitive_words.json`: `{"words": [{"value": "John Smith", "category": "name"}, {"value": "123 Main St", "category": "address"}, ...]}` 
  - [ ] 1-2. Auto-detection patterns: regex-based detection of common PII formats (email addresses, phone numbers, SSNs, credit card numbers, IP addresses) — runs in addition to user-defined words
  - [ ] 1-3. Category taxonomy: `name`, `address`, `email`, `phone`, `password`, `financial`, `id_number`, `custom`
  - [ ] 1-4. `/pii add "John Smith" --category name` and `/pii list` and `/pii remove "John Smith"` chat commands
  - [ ] 1-5. `DAN_PII_PROTECTION=1` env var to enable (default off — opt-in for privacy-conscious users)

- [ ] 2. **Tokenizer (outbound)**
  - [ ] 2-1. `PIITokenizer` — scans outgoing LLM prompts for registered sensitive words and detected PII patterns
  - [ ] 2-2. Replacement strategy: semantic placeholders, NOT random hashes. `John Smith` → `[PERSON_1]`, `123 Main St` → `[ADDRESS_1]`, `password123` → `[PASSWORD_1]`. The LLM understands these as substitutions and reasons about them correctly.
  - [ ] 2-3. Word-boundary-aware matching: "John" should match "John" but not "Johnson". Possessives ("John's") and contractions should tokenize to "[PERSON_1]'s" — preserve the grammatical suffix, replace only the PII. Use `\b` regex boundaries with suffix-aware capture groups.
  - [ ] 2-4. Case-insensitive matching with original-case preservation in the mapping table
  - [ ] 2-5. Deterministic mapping: same word always maps to the same placeholder within a session (maintain a `PIISession` mapping table)
  - [ ] 2-6. Multi-word support: "John Smith" matched as a phrase before individual words; longest-match-first ordering

- [ ] 3. **Detokenizer (inbound)**
  - [ ] 3-1. `PIIDetokenizer` — scans LLM responses for placeholders and restores original values
  - [ ] 3-2. Handle variations: `[PERSON_1]`, `PERSON_1`, `Person_1` — normalize before lookup
  - [ ] 3-3. Leak detection: scan response for original sensitive words that weren't replaced (shouldn't happen, but defense-in-depth) — log warning if found
  - [ ] 3-4. Context-aware restoration: if the LLM generates new text around a placeholder, ensure the restored text fits grammatically (heuristic — insert the original value in place of the placeholder token)

- [ ] 4. **Integration hooks**
  - [ ] 4-1. Hook into `LLMExecutor` — tokenize before `provider.complete()`, detokenize after
  - [ ] 4-2. Hook into `ChatManager.send_message()` — tokenize user message before LLM call, detokenize response before displaying
  - [ ] 4-3. Hook into tool calls: if a tool call argument contains PII, tokenize; if a tool result contains PII, don't tokenize (tool results are local data, not sent to LLM unless in a follow-up)
  - [ ] 4-4. Scope: only applies to external API calls — local processing, file I/O, and on-device operations are unaffected

- [ ] 5. **Session management**
  - [ ] 5-1. `PIISession` — per-conversation mapping table: `{placeholder: original_value}`. Persisted in memory only (never written to disk in plaintext).
  - [ ] 5-2. Session lifecycle: created on first tokenization, cleared on conversation end or explicit `/pii clear-session`
  - [ ] 5-3. Cross-turn consistency: the same word gets the same placeholder across all turns in a conversation

- [ ] 6. **Tests and docs**
  - [ ] 6-1. Unit tests: registry CRUD, tokenization (single word, multi-word, regex patterns, word boundaries, case sensitivity), detokenization, leak detection
  - [ ] 6-2. Integration test: end-to-end message flow with PII — verify original words never appear in the outgoing API call
  - [ ] 6-3. Edge cases: PII in code blocks (should tokenize?), PII in tool arguments, PII in system prompts
  - [ ] 6-4. Update architecture, changelog, `.env.example`

## Dependencies

- `LLMExecutor` for outbound hook
- `ChatManager` for chat-path hook
- `ProviderRegistry` — tokenization happens at the provider boundary
- `Concierge` for `/pii` command dispatch

## Estimate

2 days

## Notes

- **Semantic placeholders, not hashes.** `[PERSON_1]` works because LLMs understand it as a variable substitution. Random hashes like `a3f8b2c1` confuse the model and degrade response quality.
- **Opt-in by default.** PII protection adds latency (regex scanning) and may occasionally cause issues with code that contains matched patterns. Users who need it enable it explicitly.
- **Never persist the mapping to disk.** The `PIISession` mapping table exists only in memory. If someone compromises the disk, they can't recover the PII↔placeholder mappings.
- **This is not a full DLP solution.** It's a pragmatic first step for personal use. Enterprise-grade DLP would require tokenization at the network layer, audit logging, and compliance features.
- **Code blocks:** By default, PII inside fenced code blocks is still tokenized (code often contains hardcoded credentials). Users can opt out with `DAN_PII_SKIP_CODE_BLOCKS=1` if tokenization breaks their code generation workflows.
