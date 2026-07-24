# Meeting Mode, Adaptive Task Blueprints, and Manufacturing — Long-Horizon Product Thesis

**Status:** Meeting/manufacturing incubation; the horizontal Task Blueprint substrate is implemented via Plan 59.
**Last reviewed:** 2026-07-21
**Research scope:** Current public service offerings were reviewed from official provider pages. Provider claims, prices, minimums, availability, and legal requirements must be rechecked before any real order.

**Implemented horizontal boundary:** [Plan 59](../plans/59-adaptive-task-blueprints.md) now provides the provider-neutral adaptive Task Blueprint, separate Execution Attempt, Super DAN bridge, and typed Work GUI projection described in Section 2. Meeting-room collaboration, manufacturing packets/providers, quoting, purchasing, sample release, and production remain incubation-only and are not authorized by that substrate.

## Thesis

DAN could eventually become a shared creative room where people talk while agents perform visible work, then extend the same experience from software into physical products:

> group conversation → product intent → engineered production package → manufacturability and compliance review → prototype quote → approved sample order → revision → verified production order

The promising product is not a universal AI image generator and not an “Uber for every factory.” It is a **manufacturing compiler and broker**: preserve the intent and decisions from a group session, translate them into the different technical packages each production process requires, route those packages to existing manufacturers, and keep humans in control of specifications, spending, and external orders.

The market already contains most of the manufacturing endpoints. The missing layer is the trustworthy bridge from an informal idea to a validated, orderable package, especially when a finished product spans several processes or suppliers.

Two horizontal ideas make that bridge more general than manufacturing: DAN could compile each request into a task-native, revisable graph rather than forcing every job through one workflow, and it could project that graph into a task-appropriate interaction surface so users can understand the work and supply the next useful input naturally.

## 1. Concept Ladder

| Layer | Long-term idea | Durable output |
|---|---|---|
| **Meeting Mode** | Several people and agents share a live room. People talk naturally while agents research, edit, prototype, calculate, and update a visible workboard in parallel. | Transcript, decisions, constraints, open questions, approvals, and provenance-linked work products |
| **Group vibe coding** | Humans and coding agents explore one product together through isolated workstreams or branches, with visible ownership and review before merge. | Working software, tests, design assets, and a decision ledger |
| **Vibe blueprinting** | The group describes a physical object in ordinary language and sketches; specialist agents turn the intent into engineering candidates and category-specific production files. | A reviewable production package, not merely a render or concept image |
| **Manufacturing broker** | The system checks order readiness, obtains comparable quotes, coordinates prototypes and feedback, and routes approved work to appropriate manufacturing services. | Quote set, approved order packet, sample inspection record, and revision history |

Meeting Mode should be a collaborative surface over DAN's existing human-interaction and agent-team concepts, not a separate orchestration architecture. Its distinguishing behavior would be that conversation and execution occur together: participants can see what is being attempted, interrupt or redirect it, and tell the difference between an idea, a decision, a task, and an approval.

### Meeting Mode principles

- Maintain one shared live workboard while allowing separate human and agent workstreams.
- Attribute statements, decisions, edits, and approvals to their source.
- Isolate experimental code/design branches until a participant approves merge or promotion.
- Turn speech into proposed constraints and tasks, never silent commitments.
- Require explicit approval for irreversible external actions, especially purchases, supplier disclosure, and production orders.
- Preserve a compact digital thread from the original conversation through every blueprint revision, quote, sample, and acceptance decision.

## 2. Adaptive Task Blueprints and Fluid Task Surfaces

`Plan → execute → validate` is a useful reusable pattern, but it should be one possible graph shape rather than DAN's universal ontology. A **dynamic task blueprint** would interpret the goal, constraints, risk, available evidence, and expected artifact, then propose the smallest useful semantic task graph for that particular job. The graph remains inspectable and revisable as new evidence appears.

### Task-native graph shapes

| Task family | Illustrative topology |
|---|---|
| **Direct answer or small action** | Understand → retrieve only if needed → answer or act; no ceremonial planning stage |
| **Debugging or repair** | Observe → reproduce → branch into hypotheses and probes → patch → focused checks; loop when a hypothesis fails |
| **Research** | Decompose questions → collect evidence in parallel → connect claims to sources → synthesize → review coverage and uncertainty |
| **Creative or design work** | Gather references and constraints → generate parallel variants → critique and select → iterate → package the chosen artifact |
| **Meeting and group work** | Listen and capture → identify decisions and parallel workstreams → reconcile conflicts → merge or request approval |
| **Physical-product creation** | Preserve intent → produce process-specific engineering projections → DFM/compliance review → compare quotes → approve sample |

The blueprint should be able to add, remove, split, merge, reorder, or reconnect nodes; open or collapse branches; run safe work in parallel; and insert a targeted review when a newly discovered risk requires one. These changes should retain stable task identities, revision history, dependencies, provenance, and a clear explanation of why the graph changed. Nodes represent user-meaningful work, artifacts, decisions, or gates—not every internal tool call.

### Stable contract, flexible topology

| Stable across task types | Allowed to vary with the task |
|---|---|
| User goal, constraints, and non-goals | Number, names, and sequence of nodes |
| Permission and safety envelope | Branches, loops, parallelism, and stopping rules |
| Evidence and artifact references | Placement of specialists, reducers, and validators |
| Completion, acceptance, and approval criteria | Task-specific views and request-input controls |
| Status meanings, provenance, and revision history | How much of the graph is shown or progressively disclosed |

This extends DAN's existing distinction between broad run phases and the semantic live task graph: broad phases can remain useful for orientation and telemetry without dictating the task's actual reasoning structure.

### A fluid frontend for each task

The frontend should be a **typed projection of the current task graph, artifact, and decision point**. The surrounding shell can remain familiar—conversation, task map, artifacts, provenance, and approvals—while the inner work surface adapts to what the user is trying to understand or provide.

Examples include a diff and focused-test view for code repair; a claim/source matrix for research; filters, charts, and assumption controls for data work; transcript, decisions, and workstreams for meetings; a variant canvas for design; and specification, quote, DFM, and sample-approval views for manufacturing.

Request input should adapt in the same way:

- Keep free-form conversation available everywhere.
- Reveal structured inputs only when they reduce ambiguity: files, reference selectors, constraints, dimensions, choices, sliders, approvals, or comparison controls.
- Ask for the smallest missing decision rather than presenting a large generic form up front.
- Write each interaction back into the blueprint as a typed constraint, decision, artifact, or steering event so the graph and interface cannot silently diverge.
- Let users collapse, pin, override, or return to a plain conversational fallback.

“Fluid” should mean composition from a trusted, accessible component grammar—not arbitrary generated frontend code for every request. That boundary keeps navigation, permissions, state persistence, responsiveness, and provenance predictable while still making each task easier to understand and steer. This is a long-term interaction thesis, not an implementation proposal.

## 3. “Blueprint” Is Not One File Format

An attractive render can communicate intent, but it is not normally sufficient for quotation or production. Each product family has its own compilation target.

| Product family | Typical factory-ready package | Current ordering reality |
|---|---|---|
| **3D-printed part** | STL/3MF/STEP or another accepted 3D model, units, material, process, finish, orientation or critical-surface notes, and quantity | Often self-serve and economical at quantity one |
| **CNC or molded part** | Solid CAD, dimensioned drawing, tolerances, threads, material, finish, inspection requirements, and quantity | CNC prototypes can be one-off; complex features and molding commonly trigger engineering review and tooling cost |
| **Laser-cut or formed sheet part** | DXF/SVG/STEP geometry, thickness, material, bend/tap/finish instructions, tolerances, and quantity | Frequently instant-quoted for one part, but the result is still a component rather than an assembled product |
| **Electronics or small gadget** | Schematic, PCB layout/Gerbers, drill data, BOM with exact part numbers, pick-and-place file, firmware, enclosure CAD, assembly instructions, and test procedure | Low-volume PCBs and assemblies are accessible, but enclosure, firmware, final assembly, certification, and packaging remain separate workstreams |
| **Original garment** | Tech pack, patterns, measurements, materials and trims, colorways, graded sizes, labels, construction details, and approved fit sample | Surface printing can be one-off; truly new garment geometry normally requires human development, sampling, and a production MOQ |
| **Cutlery, utensils, or tableware** | CAD/drawings or ceramic model, exact material/grade, forming or molding process, finish/coating, food-contact and cleaning requirements, packaging, inspection plan, and quantity | A one-off prototype may be possible, but production usually becomes a tooling- and QA-heavy RFQ with much higher quantities |

Current platforms reinforce this distinction. [MFG.com](https://knowledge.mfg.com/how-do-i-create-an-rfq) asks for CAD plus material, finish, tolerances, quantities, and delivery expectations; [Sewport](https://sewport.com/) describes the separate tech-pack, sample, and size-grading stages for clothing; and [PCBWay](https://www.pcbway.com/pcbdesign/quotelayout) identifies Gerber, BOM, coordinate, and drill files for PCB work.

## 4. Are “Gig Factories” Already Real?

**Yes, within bounded categories. No, not yet as one consumer-facing service for arbitrary finished light-industry products.** The closest services fall into distinct islands.

| Market island | Current examples | What already works | Boundary |
|---|---|---|---|
| **Broad CAD-to-part networks** | [Xometry](https://www.xometry.com/how-xometry-works/), [Protolabs Network](https://www.hubs.com/), [Fictiv](https://www.fictiv.com/), [3DEXPERIENCE Make](https://www.3ds.com/make/solutions/on-demand-manufacturing) | Upload production-ready CAD, select a process/material, receive instant or managed quotes, and order one-off or low-volume CNC, 3D-printed, sheet-metal, cast, or molded parts | Engineering-oriented; usually makes parts, not an arbitrary tested and packaged consumer product |
| **Literal fabrication marketplaces** | [Craftcloud](https://support.craftcloud3d.com/en/articles/210-how-does-craftcloud-work-and-what-services-are-provided), [Treatstock](https://www.treatstock.com/l/start-3dprintservice) | Compare providers or route jobs to distributed machine owners; strong for one-off 3D printing and some CNC/cutting work | Narrow process vocabulary; quality, shipping, and capability vary by provider |
| **Direct one-off digital fabrication** | [Sculpteo](https://www.sculpteo.com/en/services/online-3d-printing-service/), [SendCutSend](https://sendcutsend.com/home-v/), [Ponoko](https://www.ponoko.com/) | Upload a model or vector/solid CAD, receive automated checks and prices, and order one or many parts | Additive or sheet-oriented components; assembly and end-product validation are outside the order |
| **PCB and electronics production** | [PCBWay](https://www.pcbway.com/), [MacroFab](https://www.macrofab.com/platform), [Seeed Fusion](https://www.seeedstudio.com/pcb-assembly.html), [JLCPCB](https://jlcpcb.com/) | Low-volume bare boards and populated PCB assemblies can be quoted and ordered from technical files | Does not automatically supply product definition, firmware, enclosure integration, whole-product testing, certification, or retail packaging |
| **Catalog-constrained print on demand** | [Printify](https://printify.com/custom-products/), [Printful](https://www.printful.com/custom-clothing), [Contrado](https://www.contrado.com/cut-and-sew-manufacturers) | Consumer-friendly design tools, no-minimum ordering, fulfillment, and large catalogs of clothing, homeware, and merchandise | The user usually changes artwork or panels on a predefined product; the base geometry and production recipe are owned by the platform |
| **True custom apparel and goods sourcing** | [Sewport](https://sewport.com/), [Maker's Row](https://app.makersrow.com/) | Connect a brand or designer with development studios and factories for proposals, samples, and production | Human/RFQ workflow; supplier-specific sampling costs, lead times, and MOQs |
| **Broad supplier and RFQ markets** | [MFG.com](https://knowledge.mfg.com/what-is-mfg-and-how-do-i-use-it), [Alibaba RFQ](https://rfq.alibaba.com/rfq/rfqForm.htm), [Thomasnet](https://www.thomasnet.com/) | Reach many factories across materials and product categories with one sourcing request | Procurement marketplace rather than automatic compilation, validation, or checkout; the buyer still qualifies suppliers and negotiates terms |
| **Collaborative or decentralized precedents** | [Wikifactory](https://wikifactory.com/platform/manufacture/), [3DOS](https://3dos.io/), [Asmbly Labs](https://asmblylabs.com/), [Fab City](https://fab.city/) | Product-data collaboration, distributed design-to-production visions, and emerging local 3D-print routing | Either engineering-assisted, early-stage, community infrastructure, or limited mainly to digital fabrication |

This scan found no established platform that will accept an arbitrary consumer idea or rough blueprint for clothing, cutlery, utensils, electronics, and other light-industry products, then automatically return a safe, compliant, assembled, tested, packaged one-off. That conclusion is an inference from the fragmented service boundaries above, not a claim that no small concierge or regional factory could perform such work manually.

### Category-specific answer

- **3D-printed objects:** already close to the desired model. A consumer can upload a valid model and order one object from several providers.
- **Laser-cut, waterjet, and simple machined objects:** also mature for one-off parts when the CAD and material specification are valid.
- **Clothes:** one-off printing on existing blanks is mature. Original silhouettes and construction move into garment development, tech packs, fit samples, and usually MOQs. Specialist ateliers can make a one-off, but at bespoke-service economics.
- **PCBs and small electronics:** prototypes are very accessible from Gerbers/BOMs, but a finished gadget is a supply-chain project combining electronics, mechanics, firmware, assembly, testing, and compliance.
- **Cutlery and utensils:** prototypes can be printed or machined, and individual factories can make samples. Production-grade stamped, forged, molded, polished, coated, or ceramic goods usually require manual RFQs, tooling, food-contact review, and larger production quantities.
- **“Every light-industry product”:** supplier discovery exists, but universal self-serve production does not. The diversity of specifications, processes, assembly chains, and legal obligations prevents one simple upload contract.

Concrete exceptions illustrate the continuum rather than a universal platform. [The Evans Group](https://tegmade.com/faq/) advertises managed original-garment production from 1–50 pieces per style with no in-house minimum, after design/development and sampling work. [Siam Ceramics](https://siamceramics.com/1-piece-ceramic-porcelain-sample-service/) advertises a manually reviewed one-piece ceramic or porcelain sample service. [XR Cutlery](https://xrcutlery.com/oem-odm/) describes a custom prototype-and-approval stage, but lists a standard production MOQ of 5,000 pieces per flatware design. These are useful precedents, not endorsements or guaranteed current quotes.

## 5. Why the Universal Version Is Hard

1. **The input languages differ.** Geometry, tolerances, garment grading, BOMs, firmware, glazes, coatings, labels, and test procedures cannot be represented reliably by one generic image or mesh.
2. **Instant quotes require a bounded process.** Automated pricing works when geometry, material, operations, and quality rules are constrained. Complex assemblies and unusual requirements revert to engineering review or an RFQ.
3. **A finished product may span several factories.** A small gadget can require boards, components, enclosures, fasteners, finishing, assembly, programming, testing, packaging, and freight from different suppliers.
4. **Setup and tooling dominate many one-offs.** Digital printing and additive manufacturing tolerate quantity one; stamping, forging, molding, custom textiles, and some ceramics have setup or tooling economics that push viable quantities upward. Wikifactory, for example, presents injection molding as suitable around 1,000 or more parts while supporting digital processes at prototype scale ([manufacturing overview](https://wikifactory.com/platform/manufacture/)).
5. **Manufacturability is not product correctness.** A DFM check may show that a file can be machined or printed; it does not prove that the object works, is durable, is safe, or satisfies the intended use.
6. **Compliance and accountability follow the finished product.** In the United States, manufacturers or importers of applicable products may have testing and certification duties ([CPSC](https://www.cpsc.gov/Business--Manufacturing/Testing-Certification/General-Use-Products-Certification-and-Testing)); the regulatory status of food-contact materials depends on their constituent substances and conditions of use ([FDA](https://www.fda.gov/food/packaging-food-contact-substances-fcs/determining-regulatory-status-components-food-contact-material)). A platform must establish who is manufacturer or importer of record and who owns recall, warranty, and reporting duties in each jurisdiction.
7. **Distributed production complicates IP and quality control.** Production files must be disclosed to a supplier, while consistent results require qualification, inspection, traceability, change control, and a fair rework/refund process.

## 6. Product Interpretation for DAN

The strongest role for DAN is an orchestration layer above existing factories, not a factory owner at the beginning.

### The potential compiler

A future provider-neutral `Manufacturing Packet` could conceptually contain:

- the intended use and explicit non-goals;
- source conversation, decisions, constraints, and unresolved assumptions;
- revisioned design files and drawings;
- materials, tolerances, finishes, quantities, and target cost;
- BOM, firmware, assembly, and test artifacts where applicable;
- regulatory/compliance questions and evidence status;
- DFM findings, exceptions, and required human sign-offs;
- supplier-safe disclosure view, NDA/IP status, and export restrictions;
- quote comparison, sample acceptance criteria, inspection results, and order approval.

This is a conceptual interoperability target, not a proposed API or implementation commitment. The compiler would produce different process-specific projections from the same traceable product intent.

### Specialist roles inside a group session

Meeting Mode could eventually let specialist agents work as an industrial designer, mechanical engineer, electronics engineer, garment technologist, manufacturing engineer, cost engineer, sourcing specialist, and compliance reviewer. Their outputs should be bounded artifacts and review comments, not an unstructured group chat that silently converges on an order.

### Provider relationship

- Use existing instant-quote services for well-bounded parts.
- Use RFQ marketplaces or human concierges where specifications or processes are not automatically quoteable.
- Split multi-process products into traceable sub-orders only when one party owns final assembly and acceptance.
- Preserve alternative suppliers and quotes without pretending they are technically equivalent.
- Keep the factory/manufacturing partner responsible for process execution while making design ownership, importer/manufacturer-of-record status, and warranty responsibility explicit.

## 7. Safety and Scope Boundaries

If this idea is revisited, the initial scope should be **prototype facilitation**, not autonomous consumer-product launch.

- Never translate a concept image directly into a paid production order.
- Require explicit human approval for design release, supplier disclosure, quote acceptance, payment, prototype acceptance, and production scale-up.
- Start with unregulated, non-safety-critical, digitally manufacturable objects.
- Exclude food-contact products, children's products, medical uses, weapons, load-bearing/safety-critical parts, batteries, mains electricity, and other regulated categories until specialist review and a clear manufacturer-of-record model exist.
- Treat generated engineering files as unverified until process-specific DFM and qualified human review are complete.
- Order and inspect a prototype before any batch order; record deviations and feed them back into the revisioned package.

## 8. Sensible First Wedge, If Revisited

The least misleading experiment would be a **manual, prototype-only concierge for simple desk objects or fixtures** made from one 3D-printed, laser-cut, or easily machined part. This deliberately avoids claiming that DAN can manufacture arbitrary finished goods.

A later small-gadget experiment could combine an existing/off-the-shelf electronics module with a custom enclosure, but only after assembly ownership, firmware, testing, labeling, and compliance responsibilities are explicit. Full custom clothing, cutlery, food-contact goods, children's products, and mains-powered electronics should not be early wedges.

## 9. Questions to Resolve Before Any Plan

- Do groups actually want to create physical products during the same collaborative session in which they ideate and code?
- Which narrow product family has repeat demand and enough standardization for reliable compilation and quotation?
- Can generated packages pass provider DFM with no more than one bounded human correction cycle?
- Which providers expose stable quoting or ordering APIs, and which require browser/RFQ/human workflows?
- Who owns design verification, product liability, importer/manufacturer-of-record duties, warranties, and recalls?
- How are supplier capability, certifications, material traceability, inspection, and repeatability verified?
- What is the acceptable cost and time for one prototype, including engineering, shipping, tariffs, rework, and testing?
- How will the platform protect IP while giving factories enough information to quote and produce accurately?
- For a multi-part product, who performs final assembly, programming, end-of-line testing, packaging, and acceptance?
- Is this best kept as a DAN capability, built as a separate product using DAN, or offered through a manufacturing partner?

## 10. Validation Sequence, Not a Roadmap

No implementation should begin from this note alone. If the thesis is revisited, validate it in this order:

1. Observe real group ideation sessions and test whether Meeting Mode captures decisions and concurrent work better than ordinary chat plus project tools.
2. Manually convert a few simple, non-regulated ideas into complete production packets with an experienced designer or manufacturing engineer.
3. Submit the same packet to multiple providers and measure quoteability, clarification rounds, DFM failures, price spread, and lead time.
4. Produce and inspect one prototype; compare it against explicit acceptance criteria rather than visual resemblance.
5. Repeat after a design revision to test whether the digital thread prevents specification drift.
6. Only then decide whether provider integrations, broader product categories, or actual order routing deserve an implementation plan.

## Working Vocabulary

- **Meeting Mode:** multi-human, multi-agent conversation with concurrent visible work and a decision/approval ledger.
- **Group vibe coding:** collaborative software creation in which humans and agents explore in parallel under branch, provenance, and merge controls.
- **Dynamic task blueprint:** a task-native, revisioned semantic graph whose topology follows the request, evidence, risk, and intended artifact instead of a mandatory phase template.
- **Fluid task surface:** a stable interaction shell with task- and decision-specific typed views and input controls projected from the current blueprint.
- **Vibe blueprinting:** conversational generation of reviewable engineering candidates and production artifacts; not image-to-order automation.
- **Manufacturing compiler:** translation from product intent into category- and process-specific production packages plus validation results.
- **Manufacturing broker:** supplier discovery, quoting, sample, and order coordination over existing providers.
- **Gig factory:** informal umbrella term for distributed manufacturing capacity that accepts externally supplied designs; established industry terms include manufacturing-as-a-service, cloud manufacturing, on-demand manufacturing, and distributed manufacturing.
