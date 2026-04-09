# Launch Roadmap — Incorporation to Product Launch

**Last updated:** 2026-03-27

This document covers every step from company formation to product launch. Phases are roughly chronological but many steps can run in parallel.

---

## Phase 0 — Entity Formation (Week 1–2)

### 0.1 Wyoming LLC Formation

**Why Wyoming:**
- $100 filing fee (vs. $90 Delaware but with $300/yr franchise tax)
- $52–60/yr annual report (vs. Delaware's $300/yr minimum franchise tax)
- No state income tax, no gross receipts tax
- Strong privacy — no public disclosure of members/managers
- Can re-domesticate to Delaware later if raising institutional capital

**Steps:**

- [x] **Choose company name** — ~~check availability at wyoming.gov/business~~
  - **Decision: Liberate AI LLC** (approved 2026-03-27)
  - Motto: "Let Intelligence Bear Every Routine And Tedious Effort"
  - Chinese brand: 利博 (Lìbó) / tagline: 释繁归简
- [x] **File Articles of Organization** — filed via Wyoming Agents ($154 incl. registered agent)
  - Approved same day (2026-03-27)
- [x] **Appoint a registered agent** — Wyoming Agents (included in filing package)
- [x] **Draft Operating Agreement** — template created at `docs/business/operating-agreement-liberate-ai-llc.md`
  - Needs: fill in blanks (name, address, contribution values) and sign
- [x] **Draft IP Assignment Agreement** — template created at `docs/business/ip-assignment-agreement.md`
  - Needs: fill in blanks and sign concurrently with Operating Agreement
- [ ] **Get EIN from IRS** — call +1 (267) 941-1099 (international EIN line)
  - Best time: 6:00–8:00 PM HKT (6:00–8:00 AM ET)
  - Have Form SS-4, Articles of Organization, and passport ready
  - EIN given verbally on the call; CP 575 letter arrives by mail in 4–6 weeks
- [ ] **Open business bank account** — need EIN + Operating Agreement + Articles
  - Recommended: Mercury (mercury.com) — startup-friendly, no branch visit, accepts foreign founders
- [ ] **Sign Operating Agreement and IP Assignment** — fill blanks, sign, keep as PDF

**Total cost: ~$150–250 (filing + registered agent)**
**Timeline: 1–2 weeks**

---

## Phase 1 — IP Protection (Week 2–6, parallel with Phase 0)

### 1.1 Provisional Patent Application

**What to patent:**
- The structured spec-driven, parallel candidate-graph workflow generation pipeline (Plan 44)
- Typed-edge schema validation at design time with output normalization
- Three-surface authoring (DSL + visual + markdown) compiling to a single IR
- Composable sub-graph architecture with two-level abstraction (operator → agent)

**Process:**
- [ ] **Document the invention** — write detailed technical specification covering:
  - The problem (existing builders can't do while-loops, typed edges, composability)
  - The architecture (typed graph with composable sub-graphs, output normalization layer)
  - The novel methods (NL→spec→sectioned candidate graph→validated graph pipeline)
  - Include diagrams, data flow, algorithm descriptions
- [ ] **File provisional patent** at USPTO Patent Center
  - Filing fee: $65 (micro entity) or $130 (small entity)
  - Micro entity: <4 prior patents, income under threshold, not assigned to a large entity
  - No formal claims required, but specification must be detailed enough to support later claims
- [ ] **Set calendar reminder: 12 months** to convert to non-provisional or lose priority date
- [ ] **Consider filing 2–3 separate provisionals** for distinct inventions:
  - (A) Structured workflow generation pipeline (NL → spec → sectioned graph → acceptance)
  - (B) Output normalization as automatic layer (parse → validate → re-prompt → retry)
  - (C) Three-surface authoring with single IR compilation

**Cost: $65–390 (DIY) or $3,000–6,000 (with patent attorney)**
**Recommendation:** File DIY provisionals now to secure priority date. Hire attorney later for non-provisional conversion if the product gains traction.
**Timeline: 2–4 weeks to prepare and file**

### 1.2 Trademark

**What to trademark:**
- Product name (e.g., "DAN", "Deep Agent Network", or chosen brand name)
- Logo (if created)

**Process:**
- [ ] **Search USPTO TESS** for conflicts — [tess2.uspto.gov](https://tess2.uspto.gov)
  - "DAN" alone is likely too generic / already taken — need a distinctive mark
  - Consider a more distinctive brand name for trademark purposes
- [ ] **File trademark application** — $350 per class (Class 42: SaaS; Class 9: downloadable software)
  - Intent-to-use basis if not yet selling; use-in-commerce if already selling
  - Statement of Use fee: $150/class (due when you start selling)
- [ ] **Timeline:** 8–10 months to examination, ~12–18 months to registration

**Cost: $350–700 (1–2 classes, DIY) + $150/class Statement of Use**
**Recommendation:** File intent-to-use application early. The 8-month examination wait means filing now aligns with a Q4 2026 / Q1 2027 launch.

### 1.3 IP Assignment

- [ ] **Assign all existing code/IP to the LLC** via a written IP Assignment Agreement
  - Critical: without this, the IP legally belongs to you personally, not the company
  - Template available from Cooley GO, YCombinator SAFE docs, or a startup attorney
  - This is required if you ever raise money or sell the company

---

## Phase 2 — Product Readiness (Week 4–16)

### 2.1 Core Product Gaps to Close Before Launch

Based on the development plan, these are the minimum-viable features for a paid product:

- [ ] **Plan 44: Structured Workflow Generation** — the core differentiator
  - NL prompt → typed spec → sectioned candidate graph → validated runnable workflow
  - This is what makes DAN different from "yet another drag-and-drop builder"
- [ ] **Templates library** — 10–20 pre-built workflow templates for common use cases
  - Email processing, content generation, data analysis, research, code review
  - Templates reduce time-to-value and demonstrate capabilities
- [ ] **Hosted deployment** — users need a way to run workflows without self-hosting
  - Minimum: managed backend with user accounts, workflow storage, run history
  - Options: single-tenant VPS, multi-tenant cloud (AWS/GCP), or Vercel + serverless
- [ ] **Authentication and multi-tenancy** — user accounts, API keys, workspace isolation
- [ ] **Usage metering and billing integration** — Stripe for subscriptions
- [ ] **Documentation site** — quickstart, API reference, tutorials, cookbook
  - Use Mintlify, Docusaurus, or Nextra

### 2.2 Polish and Trust Signals

- [ ] **Landing page** — clear value proposition, demo video, pricing, waitlist/signup
- [ ] **Demo video** (60–90 seconds) — show a real workflow being built from NL prompt
- [ ] **Example gallery** — 5–10 showcase workflows with results
- [ ] **Status page** — uptime monitoring (Betterstack, Instatus)

---

## Phase 3 — Pricing and Business Model (Week 8–12)

### Pricing Strategy

**Recommendation: Open-core with usage-based cloud pricing**

| Tier | Price | Includes |
|------|-------|----------|
| **Community** | Free (self-hosted) | Full engine, visual editor, CLI, unlimited local runs |
| **Pro** | $49/mo | Cloud hosting, 5,000 workflow runs/mo, 3 concurrent, run history, email support |
| **Team** | $149/mo | 20,000 runs/mo, 10 concurrent, team workspaces, API access, priority support |
| **Enterprise** | Custom | Unlimited, SSO, audit log, SLA, dedicated instance, on-prem option |

**Why this model:**
- Open-source core builds community and trust (Dify, n8n, Langflow all do this)
- Cloud hosting is the natural upsell — most users don't want to manage infrastructure
- Usage-based pricing aligns cost with value delivered
- Enterprise tier captures large accounts later

**Competitor pricing reference:**
- Dify: $59–159/mo
- Flowise: $35–65/mo
- n8n: $24/mo+
- Zapier: $19.99/mo+

### Revenue Model Alternatives

| Model | Pros | Cons |
|-------|------|------|
| **SaaS subscription** | Predictable MRR, standard for B2B | Requires hosting infrastructure |
| **Marketplace (templates/blocks)** | Network effects, community-driven | Slow to build supply side |
| **API/metered** | Pay-per-use aligns with value | Unpredictable revenue |
| **Consulting/services** | High ACV, immediate revenue | Doesn't scale, distracts from product |

**Recommendation:** Start with SaaS subscription. Add marketplace later when community is large enough.

---

## Phase 4 — Go-to-Market (Week 12–20)

### 4.1 Positioning

**Don't say:** "AI workflow orchestration platform with typed graphs"
**Do say:** "Describe your repetitive work in plain English. Get a robust, reusable automation that actually handles edge cases."

**One-liner options:**
- "Turn repetitive work into reliable AI workflows — in minutes, not months"
- "The AI workflow builder that actually handles loops, errors, and complexity"
- "From prompt to production-grade automation. No drag-and-drop fragility."

**Target ICP (Ideal Customer Profile):**
1. **Solo operators / small teams** doing repetitive multi-step tasks (research, content, data processing)
2. **AI/ML engineers** who need production-grade orchestration beyond notebook prototypes
3. **Technical founders** automating internal ops before hiring

### 4.2 Launch Channels

| Channel | Action | Timeline |
|---------|--------|----------|
| **Hacker News** | Show HN post with a compelling demo | Launch day |
| **Product Hunt** | Scheduled launch with assets prepared | Launch day |
| **Twitter/X** | Build-in-public thread leading up to launch, demo GIFs | 4 weeks before |
| **Reddit** | r/MachineLearning, r/artificial, r/SideProject posts | Launch week |
| **YouTube** | 3–5 tutorial videos showing real workflows | Launch week |
| **Dev.to / Medium** | Technical blog posts on the architecture and why it matters | 2 weeks before |
| **Discord / community** | Launch a DAN community server | 2 weeks before |
| **GitHub** | Star campaign, README polish, contributing guide | Ongoing |
| **Direct outreach** | Cold email 50 potential power users from AI Twitter/HN | Launch week |

### 4.3 Pre-Launch Waitlist

- [ ] Set up a simple landing page with email capture (Carrd, Framer, or custom)
- [ ] Offer early access / beta invites to waitlist signups
- [ ] Share build-in-public updates to build anticipation
- [ ] Target 500–1,000 waitlist signups before launch

### 4.4 Content Strategy

Weekly cadence:
1. **Build-in-public tweets** — 3–5x/week, showing real development progress
2. **Tutorial blog post** — 1x/week, solving a real problem with DAN
3. **Demo video** — 1x/2 weeks, showing a new workflow template or feature
4. **Community engagement** — daily responses in Discord, GitHub issues, Twitter

---

## Phase 5 — Infrastructure for Revenue (Week 10–16)

### 5.1 Payment Processing

- [ ] **Stripe integration** — subscriptions, metered billing, invoices
  - Stripe Atlas is an option for incorporation + Stripe setup as a bundle
  - But since you're already forming a Wyoming LLC, just connect Stripe directly
- [ ] **Tax compliance**
  - Wyoming: no state income tax, no sales tax on SaaS (currently)
  - Federal: file as single-member LLC (Schedule C on personal return) or elect S-Corp if revenue exceeds ~$40k
  - International sales: may need to handle VAT for EU customers (Stripe handles this via Stripe Tax)

### 5.2 Legal

- [ ] **Terms of Service** — required before accepting payments
- [ ] **Privacy Policy** — required (GDPR if serving EU users)
- [ ] **Acceptable Use Policy** — what users can/cannot do with the platform
- [ ] **DMCA/takedown process** — if users share workflows publicly
- [ ] Sources: YCombinator open-source templates, Termly, or hire a startup attorney ($1,500–3,000)

### 5.3 Compliance

- [ ] **SOC 2 Type I** — not needed for launch, but required for enterprise sales later
- [ ] **GDPR** — data processing agreement, data deletion capability, EU hosting option
- [ ] Both are "when you need them" — don't gate launch on these

---

## Phase 6 — Launch (Week 16–20)

### Launch Checklist

**T-minus 2 weeks:**
- [ ] Landing page live with pricing, demo, and signup
- [ ] Documentation site complete (quickstart, API reference, 5+ tutorials)
- [ ] 10+ workflow templates available
- [ ] Demo video recorded and edited
- [ ] Product Hunt launch scheduled
- [ ] Hacker News post drafted
- [ ] Twitter thread prepared
- [ ] Discord server set up with channels (general, support, showcase, feedback)

**Launch day:**
- [ ] Publish Product Hunt listing
- [ ] Post Show HN on Hacker News
- [ ] Tweet launch thread
- [ ] Post on Reddit (r/MachineLearning, r/artificial, r/SideProject)
- [ ] Email waitlist
- [ ] Monitor and respond to all feedback channels for 48 hours straight

**T-plus 1 week:**
- [ ] Publish first tutorial blog post
- [ ] Follow up with all launch-day signups who didn't convert
- [ ] Analyze usage data — which templates, which features, where do users drop off
- [ ] Fix top 3 reported issues

---

## Timeline Summary

```
Week 1-2:   Wyoming LLC formation + bank account
Week 2-6:   Provisional patent(s) + trademark application + IP assignment
Week 4-16:  Product readiness (Plan 44, templates, hosting, auth, billing)
Week 8-12:  Pricing finalized, Stripe integration
Week 12-20: Go-to-market prep (landing page, content, waitlist, community)
Week 16-20: Launch

Parallel throughout: build-in-public content, community building
```

---

## Budget Estimate (Minimum Viable Launch)

### Formation + IP (one-time, ~$685–855)

| Item | Cost |
|------|------|
| Wyoming LLC filing | $100 |
| Registered agent (first year) | $100 |
| Annual report (first year) | $60 |
| EIN | $0 |
| Provisional patent(s) (DIY, micro entity, 1–3 filings) | $65–195 |
| Trademark (1 class, intent-to-use) | $350 |
| Domain name (first year) | $10–50 |
| **Subtotal** | **$685–855** |

### Ongoing infrastructure (monthly, once product is live)

| Item | Cost |
|------|------|
| Cloud hosting | $50–200/mo |
| Landing page (Framer/Carrd) | $0–20/mo |
| Stripe fees | 2.9% + $0.30/txn |
| Documentation (Mintlify free tier) | $0 |
| Registered agent renewal | ~$8/mo ($100/yr) |
| **Subtotal** | **$50–230/mo** |

### Optional professional services

| Item | Cost |
|------|------|
| Patent attorney (per application) | $3,000–6,000 |
| Startup attorney (ToS, privacy, IP assignment) | $1,500–3,000 |

---

## Decision Points

### Wyoming vs. Delaware (revisit later)

Switch to Delaware **only when:**
- Raising institutional VC (most VCs require Delaware C-Corp)
- Going through YCombinator or similar accelerator
- Revenue exceeds $500k and you need C-Corp tax structure

You can re-domesticate from Wyoming LLC → Delaware C-Corp when the time comes. ~$1,000–2,000 in legal fees.

### When to Raise Capital

**Don't raise until:**
- Product-market fit is validated (paying users, retention data)
- You have a clear use for the capital (hiring, infrastructure, marketing)
- Revenue trajectory justifies the dilution

**Bootstrap signals that you're ready:**
- $5k+ MRR from organic growth
- >50% month-over-month retention
- Clear bottleneck that capital would solve (usually hiring)

### Solo Founder Considerations

- Limit scope ruthlessly — launch with 1 ICP, 1 use case, 1 pricing tier
- Automate everything possible (support docs, onboarding, billing)
- Community-driven support (Discord) scales better than 1:1 email
- Consider a technical co-founder if traction validates the opportunity
