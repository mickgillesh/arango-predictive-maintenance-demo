# AeroFleet Demo Cheatsheet

> Full narrative script with exact clicks and AQL: **DEMO_SCRIPT.md**

## Before the demo

### Technical setup

```bash
# 1. Ensure env is configured
cat .env.local   # ARANGO_URL, ARANGO_DB, ARANGO_USER, ARANGO_PASSWORD, OPENAI_API_KEY

# 2. Fresh data load (do this the day before, not 5 min before)
make reset       # = make load + make score

# 3. Verify
make test        # all tests green
make check       # ArangoDB connection OK

# 4. Start servers
make dev         # FastAPI :8000 + Vite :5173

# 5. Open browser
open http://localhost:5173
```

### Recommended browser state

- Open `http://localhost:5173` in a full-screen window
- Pre-load the Impact Explorer for a critical engine so the first graph traversal
  doesn't visibly load during the pitch
- Keep the ArangoDB cloud console open in a second tab for the AQL section
- Generate a maintenance plan before going live so Act 4 begins with results on screen

### Pin engines for the demo (optional)

Set `FORCED_CRITICAL=17,42` in `.env.local` to guarantee specific engine IDs appear
critical regardless of scoring. Restart the API after changing env.

---

## Demo flow

### Act 1 — Predict (Fleet Overview)

**What to show:**
- KPI tiles: critical / warning / healthy counts
- Engines sorted by predicted RUL ascending — critical ones surface at the top
- Risk bucket colour coding (red / amber / green)

**Talking points:**
- "We've ingested the NASA C-MAPSS turbofan dataset — 20,000 real sensor cycles across
  100 engines. The health scorer detects drift in 14 channels and assigns a predicted
  remaining useful life."
- "With ArangoDB as the data platform, this KPI view isn't a separate analytics silo —
  it's a live query against the same graph that stores the operational data."

**Click:** a critical (red) engine to drill in.

---

### Act 2 — Understand (Engine Detail)

**What to show:**
- Sensor trend charts for the drifting channels — axes auto-scale per sensor
- Labels showing which subsystem each sensor monitors (e.g. "HPC outlet temperature")
- Engine metadata: model, entry-into-service date, health index, risk score

**Talking points:**
- "Each sensor is already mapped to the subsystem it monitors — fan, HPC, combustor, etc.
  That mapping lives as edges in the graph, so when we ask 'what's degrading?' we get
  an answer in subsystem terms, not just raw sensor IDs."
- "The trend doesn't show a threshold breach — it shows drift *rate*. We catch it before
  the alarm goes off."

**Click:** "Impact Analysis" button.

---

### Act 3 — Ask (Impact Explorer)

**What to show:**
- Degrading subsystems, blocking parts (stock = 0 highlighted), qualified technicians
- Explain the single AQL traversal that powers the whole view

**Talking points:**
- "This is a single AQL graph traversal. From the engine we walk outward: subsystems →
  required parts → stock levels; and inward: certified technicians at this base.
  One query, four edge collections, no joins."
- "Red parts mean stock level zero — procurement has to happen before the wrench
  touches the engine. The lead time is already in the graph."

---

### Act 4 — Plan (Planning Dashboard)

**Navigate to:** `/plan` or click "Maintenance Planner →" from Fleet Overview.

#### 4a. Generate a plan

**Click:** "Generate Maintenance Plan"

The streaming agent runs for ~30–60 seconds. Watch the progress messages appear.

**What gets created:**
- Procurement work orders for engines with zero-stock blocking parts (lead time included)
- Maintenance work orders scheduled after procurement completes
- All assigned to technicians at the correct base with matching certifications
- Load distributed evenly across technicians at each base

**Talking points:**
- "The agent reads the operational graph — it knows which parts are blocking, which
  technicians are at each base, and what certifications they hold — and uses that
  context to generate a schedule that respects all three constraints."
- "Notice procurement bars come first on the Gantt, then maintenance starts once parts
  are available. Some engines skip procurement entirely because their parts are in stock."

#### 4b. Explore the Gantt chart

- Each row is one technician; bars are work orders plotted on a real calendar timeline
- **Hover** a bar to see the work order title
- **Click** a bar to open the Work Order Drawer

**Work Order Drawer shows:**
- WO key, engine ID, type badge, status badge, risk badge
- Assigned technician and base
- Start / end dates in real calendar format (Mon–Fri working hours)
- Deadline
- Required parts with stock status (green tick / red block)

#### 4c. Work order table

- Rows sorted by deadline
- **Click** any row to open the same Work Order Drawer
- Engine ID is a link to the Engine Detail page (opens without losing planner state)

---

### Act 5 — Refine with the Planning Assistant

The chat panel at the bottom of the Planning Dashboard is a **constrained AI agent**.
It can propose changes but cannot write to the database — the user must confirm in the
Pending Changes panel.

**The confirm / reject loop:**
1. Ask the assistant to change something
2. It proposes edits (yellow panel appears)
3. Click "Confirm All" to apply, or dismiss individual edits with ✕

---

## Planning assistant prompts

### The headline demo prompt

```
Gina Moore has quit, can you reassign her work orders?
```

Gina Moore = **T008**, based at **SIN**. Her only eligible replacement is
**Angie Henderson (T003)** — same base, overlapping certifications.
The agent looks up Gina by name, finds her WOs, validates Angie against
base + cert + schedule constraints, proposes all reassignments, and on
confirm both technicians' schedules are automatically repacked.

Follow-up after confirming:
```
Show me Angie Henderson's updated schedule.
```

### Exploration (no changes proposed)

```
Give me a summary of all work orders for critical engines.

Which technicians have the most work orders scheduled?

What work orders are still pending parts procurement?

Show me all open work orders at the SIN base.

Which work orders have the earliest deadlines?
```

### Reassignment (demonstrates constraint validation)

```
Reassign the work order for engine [N] to a different technician.

Who are the eligible technicians for work order [PLN-key]?
```

The assistant validates base, certification, and schedule before proposing.

**To demonstrate constraint rejection:** ask it to reassign to a technician at a
different base or without the right certification — it explains why rather than proposing.

### Schedule and fleet adjustments

```
Push the deadline for work order [PLN-key] back by one week.

Update the description of [PLN-key] to "Urgent — airworthiness directive AD-2026-07."

Change the status of [PLN-key] to closed.

Update the stock level for [part name] to 5 units — parts just arrived.

Technician [name] has moved to the JFK base.

What would happen if I retired aircraft [tail number]?
```

### Time-travel (bi-temporal history)

```
Who was originally assigned to work order [PLN-key]?
```

Surfaces the `performedBy` edge history — reassignments are preserved as
expired edges, never deleted.

---

## AQL snippets for the console

```aql
-- Gina Moore's full assignment history (expired + current)
LET gina = FIRST(FOR t IN technicians FILTER t.name == "Gina Moore" RETURN t)
FOR e IN performedBy
  FILTER e._to == gina._id
  LET wo = DOCUMENT(e._from)
  RETURN {
    wo: wo._key, engine: wo.engineId, type: wo.type,
    validFrom: DATE_ISO8601(e.validFrom * 1000),
    validTo:   e.validTo == 9999999999 ? "current" : DATE_ISO8601(e.validTo * 1000)
  }
```

```aql
-- Audit trail for a single work order
FOR e IN performedBy
  FILTER e._from == "workOrders/PLN-xxxxxxxx"
  LET tech = DOCUMENT(e._to)
  SORT e.validFrom ASC
  RETURN { technician: tech.name, from: DATE_ISO8601(e.validFrom * 1000),
           to: e.validTo == 9999999999 ? "current" : DATE_ISO8601(e.validTo * 1000) }
```

```aql
-- Multi-hop: engine → aircraft → base → certified technicians
LET eng = DOCUMENT("engines/17")
LET ac  = FIRST(FOR a IN 1..1 OUTBOUND eng installedOn RETURN a)
FOR s IN 1..1 INBOUND eng partOf
  FILTER s.name IN eng.driverSubsystems
  FOR t IN 1..1 INBOUND s certifiedFor
    FILTER t.homeBase == ac.base
    RETURN DISTINCT { name: t.name, base: t.homeBase, certs: t.certifications }
```

```aql
-- Procurement dependency chain
FOR wo IN workOrders
  FILTER wo.generatedByPlanner == true AND wo.type == "maintenance"
  LET proc = FIRST(FOR d IN 1..1 OUTBOUND wo dependsOn RETURN d)
  FILTER proc != null
  RETURN { maintenance: wo._key, engine: wo.engineId,
           depends_on: proc._key, proc_status: proc.status }
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Planning assistant returns "not configured" | Check `OPENAI_API_KEY` in `.env.local`; restart API |
| Generated plan has no work orders | Run `make score` — engines need health scores to appear at-risk |
| All engines appear healthy | Add `FORCED_CRITICAL=17,42` to `.env.local` and restart |
| Gina Moore has no work orders | Reset the plan (click "Reset Plan") then generate again |
| Reassignment fails with wrong key format | The assistant uses `find_eligible_technicians` — keys are T001–T010 |
| Google sign-in loops | Clear cookies for the domain; OAuth session may be stale |

---

## Key talking points by audience

### Technical audience
- All AQL is bind-parameterised, lives in `backend/aql.py`, zero string interpolation
- `performedBy` edges are bi-temporal: `validFrom / validTo` in Unix seconds; history
  is never deleted, only expired — enables time-travel queries
- LangGraph `create_react_agent` with tool-level enforcement — the LLM cannot bypass
  base/cert/overlap constraints even if it tries
- Technician assignment is server-side greedy (least-loaded qualified tech), not LLM-driven —
  load is guaranteed to be balanced regardless of what the LLM decides

### Business audience
- "The graph knows not just that engine 17 is degrading — it knows which technician at
  that base is certified, whether the parts are in stock, and what the lead time is."
- "The AI assistant can't make invalid assignments. It checks base, certification, and
  schedule conflicts before proposing anything — every proposal goes through a human confirm step."
- "Reassignment history is never deleted. You can always ask: who was originally
  responsible for this work order, and when did it change?"
