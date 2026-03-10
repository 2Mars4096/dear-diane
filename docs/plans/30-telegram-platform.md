# 30: Telegram Multi-Bot Platform

**Status:** completed
**Goal:** Make Telegram a first-class DAN surface with multi-bot group chats, per-bot project focus, Telegram-native UX, and zero-friction bot management.

## Problem

DAN has a working Telegram adapter (315 LOC) but it's a bare-bones HumanNode renderer — no chat-mode routing, no media handling, no concierge integration. Meanwhile the WhatsApp Web adapter (700+ LOC) is a full conversational surface with concierge dispatch, media/voice handling, and file commands.

More importantly: Telegram is the **only** platform that natively supports multiple bots in a single group chat. WhatsApp can't do this. Discord could, but Telegram's Bot API is vastly simpler and supports forum topics, message editing, reactions, and inline keyboards — all ideal for a multi-agent workspace.

## Vision

A Telegram group chat becomes a **multi-agent workspace**:

```
┌─────────────────────────────────────────────────────────────┐
│  📱 Telegram Group: "Research Lab"                          │
│                                                             │
│  👤 You: Find recent papers on supply chain resilience      │
│  🤖 @ResearchBot: Found 12 papers. Here are the top 5...   │
│                                                             │
│  👤 You: @DataBot run the equity backtest for momentum      │
│  📊 @DataBot: Running backtest... [⏳ → ✅]                 │
│  📊 @DataBot: [Sends PDF report + inline keyboard]          │
│                                                             │
│  👤 Friend: Hey @ResearchBot, what's the latest on LLMs?   │
│  🤖 @ResearchBot: Here's a quick summary...                │
│                                                             │
│  👤 You: (no @mention) What should I work on next?          │
│  🤖 @DAN: Based on your projects, I'd suggest...           │
│                                                             │
│  [Forum topic: "Equity Research"] ← DataBot owns this      │
│  [Forum topic: "Literature"]     ← ResearchBot owns this   │
│  [Forum topic: "General"]        ← DAN (default bot)       │
└─────────────────────────────────────────────────────────────┘
```

Each bot focuses on its assigned projects. Friends in the group get responses too. The right bot picks up the right message. Bots never talk over each other.

## Architecture

```
                    ┌────────────────────┐
                    │   BotFleet         │
                    │   (coordinator)    │
                    ├────────┬───────────┤
                    │        │           │
              ┌─────┴──┐ ┌──┴───┐ ┌─────┴──┐
              │ Bot A  │ │ Bot B│ │ Bot C  │
              │ token1 │ │token2│ │ token3 │
              └────┬───┘ └──┬───┘ └────┬───┘
                   │        │          │
                   └────────┼──────────┘
                            │
                   ┌────────┴────────┐
                   │   MessageRouter │
                   │  @mention →bot  │
                   │  topic   →bot   │
                   │  keyword →bot   │
                   │  default →bot   │
                   └────────┬────────┘
                            │
                   ┌────────┴────────┐
                   │    Concierge    │
                   │  (shared, one)  │
                   │  memory/tools   │
                   └─────────────────┘
```

**Single concierge, multiple bot surfaces.** All bots share memory, workflows, and tools. Each bot is a "surface" with its own identity and project scope. The `MessageRouter` decides which bot handles each message. The `BotFleet` coordinates startup, shutdown, and anti-collision.

## Sub-Plans

| # | Plan | Scope | Est. |
|---|------|-------|------|
| 30-1 | [Adapter Upgrade](30-1-adapter-upgrade.md) | Feature-parity single bot: chat-mode, media, voice, gateway, CLI | 2d |
| 30-2 | [Multi-Bot Group Chat](30-2-multi-bot-group-chat.md) | BotFleet, MessageRouter, per-bot projects, friend interactions | 3d |
| 30-3 | [Telegram-Native Features](30-3-telegram-native-features.md) | Forum topics, message editing, reactions, polls, large files, reply-to, bot commands, Mini Apps | 2d |
| 30-4 | [Bot Management](30-4-bot-management.md) | `dan-bot` CLI, config file, BotFather guide, fleet dashboard | 1.5d |

Order: 30-1 → 30-4 → 30-2 → 30-3 (foundation first, then management UX, then multi-bot, then native features).

## Key Design Decisions

- **Single concierge** — all bots share one `Concierge` instance with shared memory kernel, workflows, and tools. No per-bot concierge — that would fragment memory and project state.
- **Per-bot identity** — each bot gets its own `DAN_BOT_NAME`, personality prompt, and project assignments. The LLM sees different system prompts per bot.
- **Forum topics for projects** — when the group has forum mode enabled, each project gets its own topic thread. The assigned bot is the primary responder in its topics.
- **Anti-collision** — only one bot responds per message. The `MessageRouter` picks the winner before any LLM call. No "both bots respond and then one deletes its message" races.
- **Friends are first-class** — any human in the group can talk to any bot. The bots don't distinguish between the owner and friends (unless `allowed_chat_ids` restricts the group).
- **Message editing for streaming** — instead of sending a placeholder + new message, the bot edits its own message as the response streams in. Much cleaner UX.
- **Config-driven, not code-driven** — bot setup is `~/.dan/telegram/config.json` + `dan-bot create`. No Python needed.

## Dependencies

- `python-telegram-bot` v21+ (already an optional dep in `pyproject.toml` via `dan[messaging]`)
- Existing: `ConcurrentDispatcher`, `Concierge`, `ChatManager`, adapter framework, `identity.py`
- No new core architecture — plugs into existing concierge/dispatcher/surface patterns.

## Non-Goals (this phase)

- Discord adapter (different API, different group model — future phase)
- Bot-to-bot autonomous delegation without human message (interesting but complex — defer)
- Telegram payment integration
- Telegram game platform
- Custom Telegram client (MTProto) — Bot API is sufficient
