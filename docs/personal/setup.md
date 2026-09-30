# Personal-agent pilot setup

## Spending is settled from provider receipts
Completed chat/extraction and voice requests count the dollar cost reported by OpenRouter. Temporary holds protect the limit while a request is in progress; unused funds are released once confirmed, including between model calls. Admission can use the remaining headroom instead of requiring the old fixed per-message reservation.

Activity → AI spending limits separates confirmed costs, in-progress holds and unverified charges. Older requests without saved generation IDs cannot be reconstructed automatically; their bounds remain explicitly unverified. They are not bills and are not silently reset. Search-provider fees, account fees and other app usage are outside this ledger.

References: [OpenRouter transcription usage](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/), [generation billing metadata](https://openrouter.ai/docs/api/api-reference/generations/get-generation).

## Web research from chat or voice

Ask “Find two cozy dinner options in Central or Sheung Wan, HKD 1000 total for two; check menus and hours.” Search uses the existing Workspace provider configuration on the Mac (including `DAN_TAVILY_API_KEY`, when configured) and available fallbacks. No new search form is needed. Links appear under the reply; blocked or unreadable pages are reported as gaps.

A turn allows at most two research rounds: four queries and ten page/menu reads total. Search may use a cached result (Workspace default 15 minutes). Page reads are fresh. Search service fees are outside the model reservation and follow that provider account's limits. Model calls stay within the existing $0.305152 default reservation using an aggregate conservative byte/output ceiling, up to four attempts. Spending pause and Stop apply to research too.

Public research does not connect email, booking, payment or external calendar accounts. Sites requiring JavaScript/login may remain unreadable.

Mac hosts execution and records. Local capture, extraction (when explicitly configured), calendar download and inbox reminders do not require Google or phone access.

## Use the Mac browser version now

From the repository root, with the project Python environment available:

```sh
PYTHONPATH=src python -m dan.personal
```

On this Mac, the verified interpreter is `/opt/anaconda3/bin/python`:

```sh
PYTHONPATH=src /opt/anaconda3/bin/python -m dan.personal
```

For conversational AI, select a model and load your existing private provider settings:

```sh
PYTHONPATH=src python -m dan.personal --env-file /path/to/private/.env --model provider/model
```

The environment file supplies the existing provider key and OpenRouter base URL. Never paste keys into chat. The conversation shares extraction’s host/operator spending limits; the default conservative reservation permits six turns per UTC day. Settings are in **Activity → AI spending limits**. Without a model, Activity still provides manual records/reminders.

Open **http://127.0.0.1:4197/#personal**. The current browser build is already prepared. After future UI changes, run `npm run build:verify` from `editor/` before launching.

- The launcher runs this checkout's backend and UI together, bound only to this Mac.
- Data persists in `.personal-local/` in this worktree, excluded from Git. Keep that directory when moving/removing the worktree. Use `--data-dir /absolute/path` to choose another location.
- Existing installed-app records and its backend are not migrated or replaced. The local native build remains in `editor/release/mac-arm64/Dear Diane.app`; it still needs a compatible installed backend.
- Keep the terminal process running and the Mac awake for reminders. Ctrl+C stops execution; the same command resumes saved records and catches up due reminders.
- Paste/upload, review, calendar-file download and reminders work without Google accounts. Automatic extraction remains an explicit `DAN_PERSONAL_MODEL` opt-in and may incur provider charges; spending reservations and provider price ceilings apply as described below.
- Use `--port 4198` if 4197 is occupied. A different port is a different browser origin, so unsent browser drafts do not move automatically.

## Continuous voice

Add `--voice` to the configured launcher command, then click the microphone in Personal. Four saved presets, interruption and automatic turns are available. See [voice setup, spending and acceptance](voice.md).

## Deferred phone and account setup

The user deferred physical-phone testing. No phone, trusted HTTPS, developer account or OAuth registration is needed for the local Mac milestone. The steps below are retained for the later connected/phone stages.

### When resuming connected and phone stages

1. Create a dedicated Google test account.
2. Create a Google Cloud project named Dear Diane; enable Gmail API and Google Calendar API.
3. Configure OAuth branding/audience, keep the app in Testing and add the dedicated account as a test user. Wait for the final broker callback URI before creating the Web application OAuth client. Store its downloaded credentials outside this repository; never paste secrets into chat.
4. Install Tailscale on the Mac and a test phone; sign both into the same tailnet. Keep access private. The app's existing remote-auth boundary must remain enabled when enabling HTTPS access.
5. Have a physical iPhone and Android device available for final acceptance (borrowed test devices are sufficient). Desktop emulation cannot establish installation/push/background behavior.

Sources: [Google consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent), [web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

## Broker prerequisites

Real-account tokens require a broker under a separate OS identity, with approval enforcement outside ordinary agent tools. The user must perform any required macOS administrator/authentication steps locally. A same-user process or a hidden file is not this boundary. Real connectors stay disabled until isolation and approval checks pass.

## Release acceptance

- Google create/update requires the exact proposed account/calendar/event preview, one approved action, read-back receipt, replay protection, stale-input rejection and revoked-scope checks.
- Phone installation requires a trusted HTTPS origin, authenticated session, reconnect and keyboard/upload checks on both devices.
- The seven-day pilot requires elapsed time and ten real test commitments. Since Mac hosting was selected, test browser closure, Mac sleep/restart and explicit catch-up; do not claim an offline Mac can execute tasks.
- Push denial/delay must leave a correct inbox; provider acceptance alone is not delivery evidence.

## Extraction spending controls

Set `DAN_LLM_BASE_URL=https://openrouter.ai/api/v1` and keep `OPENROUTER_API_KEY` in the local process environment or private host configuration, never in Git.

Automatic personal extraction currently requires an explicit OpenRouter `provider/model` ID and its configured API endpoint/key. Presets and paid search plugins are not accepted. Other provider routes remain unavailable for this bounded extraction path; manual review and reminders continue to work.

Host environment settings (USD):

| Setting | Default | Meaning |
|---|---|---|
| `DAN_PERSONAL_TASK_USD` | `0.50` | Maximum reservation per extraction |
| `DAN_PERSONAL_DAILY_USD` | `2.00` | Maximum reservations per operator per UTC day |
| `DAN_PERSONAL_INPUT_USD_PER_MILLION` | `1.00` | OpenRouter input-token price ceiling |
| `DAN_PERSONAL_OUTPUT_USD_PER_MILLION` | `5.00` | OpenRouter output-token price ceiling |

Each attempt reserves two calls with at most 131,072 UTF-8 input bytes plus a conservative framing allowance, and 4,096 output tokens per call. At defaults the reservation is **$0.305152**, allowing six attempts within the daily allowance. This intentionally overestimates normal extraction and does not report actual billed cost or include account/payment/BYOK fees. Use a provider-account spending limit for an account-wide billing cap.

- A zero task/daily limit pauses new paid calls. Invalid values fail closed.
- Failed/stopped/uncertain attempts retain their reservation; no automatic refunds. Manual review and reminders do not consume this allowance.
- Queued work rechecks lowered limits and reserves the execution day if midnight passes before execution.
- Old jobs without cost records block further admission for their UTC day instead of being counted as free.
- The Personal screen shows **AI spending limits** when a model is configured. Edit limits up to the host maxima, or enable **Pause AI replies**, then save. Settings persist across restart; lowering limits does not refund reservations. A stale tab must reload before saving. The pause is checked before each model call, including repair; it cannot undo a request already sent.
- Provider request settings disable fallback and require supported parameters. Source: [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

## Run the mock calendar workflow

```sh
PYTHONPATH=src python -m dan.connectors --report /tmp/diane-mock-calendar.json
```

This uses temporary synthetic records and a mock provider. It verifies capture, review, exact approval, creation, an approved update, a simulated lost response, restart and reconciliation. It makes no network requests and does not connect an account. The report contains mock receipts, not real calendar evidence.
