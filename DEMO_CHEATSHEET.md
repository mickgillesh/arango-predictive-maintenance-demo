# AeroFleet Demo Cheatsheet

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
- Keep the ArangoDB cloud console open in a second tab — useful for showing raw
  collections and running ad-hoc AQL

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
- Sensor trend charts for the 14 drifting channels — axes auto-scale per sensor
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

### Act 3 — Ask (Impact Explorer + Chat)

**What to show:**
- Impact section: degrading subsystems, blocking parts (stock = 0 in red), qualified technicians
- Single AQL query — expand the "AQL" panel to show the traversal spans 4 edge types in one shot
- Chat panel: type a natural-language question, watch it translate to AQL and answer

**Talking points:**
- "This is a single AQL graph traversal. From the engine we walk outward: subsystems →
  required parts → stock levels; and inward: certified technicians at this base.
  One query, four edge collections, no joins."
- "The chat panel uses LangChain's ArangoGraphQAChain — the LLM introspects the live
  database schema automatically. No manual prompt engineering, no hardcoded schema."

**Good chat questions for this screen:**

```
Which technicians are certified to work on the HPC subsystem at this engine's base?

What parts are needed to repair engine [N] and are any blocking?

How many engines at JFK have a predicted RUL under 50 cycles?

Which base has the most critical engines right now?

Show me all engines that share a base with a technician named [name].
```

---

### Act 4 — Plan (Planning Dashboard)

**Navigate to:** `/plan` or click "Maintenance Planning" in the nav.

#### 4a. Generate a plan

**Click:** "Generate Maintenance Plan"

The streaming agent runs for ~30–60 seconds. Watch the progress messages appear.

**What gets created:**
- Procurement work orders for engines with zero-stock blocking parts (lead time included)
- Maintenance work orders scheduled after procurement completes
- All assigned to technicians at the correct base with matching certifications
- All scheduled Mon–Fri, 08:00–16:00, with no technician overlap

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

## Planning assistant test prompts

### Exploration queries (no changes proposed)

```
Give me a summary of all work orders for critical engines.

Which technicians have the most work orders scheduled?

Are there any technicians with overlapping work orders?

What work orders are still pending parts procurement?

Show me all open work orders at the SIN base.

Which work orders have the earliest deadlines?

How many maintenance work orders are there vs. procurement orders?
```

### Reassignment (demonstrates constraint validation)

```
Reassign the work order for engine [N] to a different technician.

Who are the eligible technicians for work order [PLN-key]?

Find an available technician for the HPC maintenance on engine [N].

[After getting an eligible list] Reassign [PLN-key] to [name].
```

The assistant will:
1. Call `find_eligible_technicians` — only returns technicians at the right base with matching certs
2. Check availability to avoid schedule overlap
3. Propose the reassignment — you confirm in the Pending Changes panel
4. On confirm: old `performedBy` edge expires (history preserved), new edge is created

**To demonstrate constraint rejection:**
Ask it to reassign to a technician at a different base or without the right
certification — it will explain why that's not possible rather than proposing it.

### Schedule adjustments

```
Push the deadline for work order [PLN-key] back by one week.

Update the description of [PLN-key] to "Urgent — airworthiness directive AD-2026-07."

Reschedule [PLN-key] to start at working hour 40.

Change the status of [PLN-key] to closed.
```

### Fleet data changes

```
Update the stock level for [part name] to 5 units — parts just arrived.

Technician [name] has moved to the JFK base.

What would happen if I retired aircraft [tail number]?
[Then] Go ahead and retire it.
```

### Time-travel queries (bi-temporal history)

```
Who was originally assigned to work order [PLN-key]?
```

This surfaces the `performedBy` edge history — reassignments are preserved as
expired edges, never deleted.

---

## What the database looks like in the cloud console

**Useful AQL snippets to run live:**

```aql
// Current schedule for one technician
FOR wo IN workOrders
  FILTER wo.generatedByPlanner == true
  FOR t, e IN 1..1 OUTBOUND wo performedBy
    FILTER t._key == "T001"
    FILTER e.validTo > DATE_NOW() / 1000
    RETURN { wo: wo._key, start: wo.scheduledHourStart, hours: wo.estimatedHours }
```

```aql
// Full performedBy history for a work order (including expired assignments)
FOR e IN performedBy
  FILTER e._from == "workOrders/PLN-xxxxxxxx"
  LET tech = DOCUMENT(e._to)
  RETURN { tech: tech.name, validFrom: e.validFrom, validTo: e.validTo }
```

```aql
// Multi-hop: engine → aircraft → base → technicians at that base
LET eng = DOCUMENT("engines/17")
LET ac  = FIRST(FOR a IN 1..1 OUTBOUND eng installedOn RETURN a)
FOR t IN technicians
  FILTER t.homeBase == ac.base
  RETURN { name: t.name, certs: t.certifications }
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Planning assistant returns "not configured" | Check `OPENAI_API_KEY` in `.env.local`; restart API |
| Generated plan has no work orders | Run `make score` — engines need health scores to be flagged at-risk |
| All engines appear healthy | Add `FORCED_CRITICAL=17,42` to `.env.local` and restart |
| Gantt bars all overlap | Run `make reset` to regenerate data with correct serial scheduling |
| Reassignment fails with wrong key format | The assistant should use `find_eligible_technicians` — keys are T001–T010 |
| `make load` breaks test_phase2 | Also run `make score` — scoring writes back to engines separately |
| Frontend TypeScript errors | Run `cd frontend && npm run build` to see full error output |

---

## Key talking points by audience

### Technical audience
- All AQL is bind-parameterised, lives in `backend/aql.py`, zero string interpolation
- `performedBy` edges are bi-temporal: `validFrom / validTo` in Unix seconds; history
  is never deleted, only expired — enables time-travel queries
- LangGraph `create_react_agent` with `InMemorySaver` for session history; tool-level
  enforcement means the LLM cannot bypass base/cert/overlap constraints even if it tries
- Health scoring uses exponential drift saturation over 14 C-MAPSS sensor channels

### Business audience
- "The graph knows not just that engine 17 is degrading — it knows which technician at
  that base is certified, whether the parts are in stock, and when the aircraft is next
  scheduled to fly."
- "The AI assistant can't make invalid assignments. It checks base, certification, and
  schedule conflicts before proposing anything — and every proposal goes through a
  human confirm step."
- "Reassignment history is never deleted. You can always ask: who was originally
  responsible for this work order, and when did it change?"
