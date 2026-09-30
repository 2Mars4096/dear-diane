# Muse app connections: implications for Dear Diane

Reviewed: 2026-09-29. Public documentation research; no authenticated Muse inspection or live connector tests. Implementation proposal: [Plan 7](../plans/7-phone-personal-agent.md).

## What is documented

| Connection path | Evidence | Implication for Diane |
|---|---|---|
| Built-in integrations | Meta describes provider APIs paired with connector-specific skills. | Separate executable adapters from instructions about when and how to use them. |
| Custom integrations | Meta supports custom API/CLI connectors; CData demonstrates its own MCP endpoint connected to Muse. | Add a controlled MCP adapter alongside first-party API adapters. |
| Websites | Muse operates a cloud browser with user takeover. | Browser actions need a distinct session and handoff contract. |
| Device data | Help explicitly mentions Apple Health and Android SMS permissions. | Some integrations need native clients; browser access alone cannot establish parity. |
| Change notifications | Help says some connectors proactively report updates. | Model event subscriptions separately from search and action capabilities. |

API/CLI and browser evidence: [Meta engineering description](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse). Device and change-notification evidence: [Meta connector help](https://www.meta.com/help/artificial-intelligence/1687253048996149/). MCP example: [CData's integration walkthrough](https://www.cdata.com/blog/connect-enterprise-data-meta-muse).

## Authorization and execution

- Meta describes separated connector workers, a credential service, and an independent permission authority. Real credentials are inserted at execution time; user approvals are bound to particular actions. Browser login and takeover pause agent control. This describes Meta's design, not a verified security guarantee. [Engineering source](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)
- Users can connect through conversation or Settings. Some Meta accounts connect through Accounts Center. Disconnecting stops exchanges but does not necessarily remove previously retained memories or history. [Help](https://www.meta.com/help/artificial-intelligence/1687253048996149/)
- Published directory submissions undergo review and end-to-end testing; Meta's help says user-created custom connectors are not reviewed. [Platform](https://muse.ai/platform), [help](https://www.meta.com/help/artificial-intelligence/1687253048996149/)

## What remains unknown

- No complete authenticated connector inventory was checked; an app's mention does not establish every supported action, account type, or region.
- Public engineering material does not specify each provider's scopes, sync cursor format, rate limits, or recovery behavior.
- MCP works in CData's documented example; that does not establish MCP as Muse's universal internal connector protocol.
- No evidence here establishes that Muse can control arbitrary installed phone apps. Distinguish website access, service APIs, device permissions, and messaging channels.

## Proposed approach for Diane

1. Normalize useful capabilities: message search, event read/write, file read/write, task CRUD, and change watching.
2. Ship typed Google adapters first and prove the same workflows with Microsoft adapters next.
3. Let an operator register reviewed MCP tools, with declared account, scopes, domains, action class, and schemas.
4. Keep browser-mediated services explicit, bounded, and testable; preserve a user handoff when automation cannot finish.
5. Separate account connection from action approval and from deletion of previously imported data.
6. Add native device bridges only after browser-first workflows demonstrate value.

This is our design recommendation; it does not claim to reproduce Muse's infrastructure or protections.
