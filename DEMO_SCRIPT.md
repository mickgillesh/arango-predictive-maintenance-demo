# AeroFleet Demo Script

**Audience:** Technical or business stakeholders
**Total runtime:** ~20 minutes (12 min live app, 8 min ArangoDB console)
**One-sentence pitch:** Real NASA sensor telemetry, a live operational graph, and an AI planning
agent — all on a single ArangoDB instance.

---

## Before you start

```bash
make reset          # fresh data (do the day before, not right before)
make dev            # FastAPI :8000 + Vite :5173
open http://localhost:5173
```

Open two browser tabs:
- **Tab 1** — `http://localhost:5173` (the app)
- **Tab 2** — ArangoDB cloud console → your database → Queries

Sign in with your `@arangodb.com` Google account when prompted.

> **Tip:** Generate a maintenance plan before the demo starts so Act 4 begins with
> results already on screen. Reset the chat session immediately before going live.

---

## Act 1 — Predict (2 min)
### Screen: Fleet Overview `/`

Walk in cold. No setup preamble.

> "We have 100 turbofan engines. The sensor data is real — it's the NASA C-MAPSS
> FD001 dataset, 20,000 flight cycles. Every engine has a predicted remaining useful
> life computed from 14 degrading sensor channels."

Point to the KPI tiles:

> "Three buckets: critical, warning, healthy. These are live queries against the
> graph — not a separate analytics store, the same database that holds the
> operational data."

Point to the RUL histogram:

> "This is the distribution of remaining useful life across the whole fleet.
> The left tail is where the urgency is."

Point to the engine table:

> "Sorted by RUL ascending — most urgent at the top. Click any critical engine."

**Click the top critical engine.**

---

## Act 2 — Understand (3 min)
### Screen: Engine Detail `/engines/:id`

> "Two things to notice. First, the sensor trend charts. These aren't threshold
> breaches — the scorer detects drift *rate* over the engine's history. We catch
> degradation before any alarm fires."

Pause on the sensor charts.

> "Second — driver subsystems. Each of the 21 C-MAPSS sensor channels is already
> mapped to the physical subsystem it monitors: fan section, HPC, combustor, and so on.
> That mapping lives as edges in the graph. So when we ask 'what's degrading?'
> the answer comes back in engineering terms, not raw sensor IDs."

Point to the driver subsystem callout.

> "Now let's ask: what does this degradation actually mean operationally?"

**Click "Impact Analysis".**

---

## Act 3 — Ask (2 min)
### Screen: Impact Explorer `/engines/:id/impact`

> "This is a single AQL graph traversal. From the engine we walk four edge
> collections simultaneously: subsystems, required parts with stock levels,
> and certified technicians at the aircraft's home base."

Point to blocking parts (red):

> "Red means stock level zero — procurement has to happen before the wrench
> touches the engine. The lead time is already in the graph."

Point to the technicians panel:

> "These are the technicians certified for the degrading subsystems *at this
> specific base*. The graph knows not just who's qualified globally, but who's
> reachable."

> "One query, four edge collections, no joins. Let's now see what it looks like
> to act on this — generate a plan for the whole fleet."

**Click "Maintenance Planner →".**

---

## Act 4 — Plan (4 min)
### Screen: Planning Dashboard `/plan`

**Click "Maintenance Planner →"** from the Fleet Overview (or navigate to `/plan`).

If the plan hasn't been generated yet, click **"Generate Maintenance Plan"** and narrate while it streams:

> "The agent reads the graph — risk buckets, blocking parts, certifications,
> base locations — and produces a schedule. Watch the progress: it's reasoning
> through each engine in priority order."

Once complete:

> "Two types of work order. Procurement bars come first — order the parts.
> Maintenance bars follow once parts are expected to arrive. Engines with parts
> in stock skip straight to maintenance."

**Hover a Gantt bar.** Show the tooltip.

**Click a bar** to open the Work Order Drawer:

> "Work order key, engine, type, status, technician, base, scheduled dates,
> deadline, required parts with stock status. Everything in one panel because
> everything is connected in the graph."

Point to the work order table below:

> "Same data, tabular view, sorted by deadline. The engine ID is a live link —
> you can drill back to the sensor data without losing your place in the planner."

---

## Act 5 — Refine (4 min)
### Screen: Planning Dashboard — Chat Panel

> "Now the interesting part. The AI planning assistant can make changes —
> reassignments, deadline adjustments, status updates — but it cannot write
> to the database directly. Every change is proposed. The human confirms."

Type in the chat panel:

```
Gina Moore has quit, can you reassign her work orders?
```

Narrate as the agent works:

> "It looks up Gina by name — no key required. Finds her current assignments.
> Then for each work order it checks: who else is at the SIN base with the
> right certifications and available capacity?"

Wait for proposals to appear in the **Pending Changes panel**:

> "Each proposal shows exactly what will change. The agent validated base,
> certification, and schedule for every single reassignment before proposing it.
> Anything that couldn't be validated was flagged with a reason."

**Click "Confirm All".**

> "On confirmation, the old performedBy edge is expired — not deleted.
> A new edge is created to the replacement technician. The history is preserved.
> And both technicians' schedules are automatically repacked to remove any gaps."

Follow up:

```
Show me Angie Henderson's updated schedule.
```

> "The agent reads the live database and returns the compacted schedule immediately."

---

## Act 6 — Under the Hood (8 min)
### Screen: ArangoDB Cloud Console → Queries

Switch to the ArangoDB tab.

> "Everything you just saw was AQL graph queries. Let me show you what's actually
> happening in the database."

---

### Query 1 — Gina's work orders before the reassignment

> "First: what did Gina's schedule look like? The performedBy edge is temporal —
> validFrom and validTo in Unix seconds. We filter on validTo to get the historical
> picture."

```aql
LET gina = FIRST(FOR t IN technicians FILTER t.name == "Gina Moore" RETURN t)
FOR e IN performedBy
  FILTER e._to == gina._id
  LET wo = DOCUMENT(e._from)
  RETURN {
    wo:         wo._key,
    engine:     wo.engineId,
    type:       wo.type,
    status:     wo.status,
    validFrom:  DATE_ISO8601(e.validFrom * 1000),
    validTo:    e.validTo == 9999999999 ? "current" : DATE_ISO8601(e.validTo * 1000)
  }
```

> "You can see both the expired edges — Gina's assignments — and the current edges
> to Angie. The history was never deleted, just closed off with a validTo timestamp."

---

### Query 2 — Reassignment audit trail

> "Time-travel query. Pick any work order that was reassigned and ask: who was
> responsible at any point in time?"

```aql
// Replace PLN-xxxxxxxx with an actual WO key from the Gantt
FOR e IN performedBy
  FILTER e._from == "workOrders/PLN-xxxxxxxx"
  LET tech = DOCUMENT(e._to)
  SORT e.validFrom ASC
  RETURN {
    technician: tech.name,
    from:       DATE_ISO8601(e.validFrom * 1000),
    to:         e.validTo == 9999999999 ? "current" : DATE_ISO8601(e.validTo * 1000)
  }
```

> "This is bi-temporal data in a graph. You get full assignment history without
> a separate audit table — the edges *are* the audit log."

---

### Query 3 — Multi-hop traversal: engine → operations → constraints

> "This is the same query that powers the Impact Explorer. One AQL statement walks
> four edge collections — no joins, no subqueries across systems."

```aql
LET eng = DOCUMENT("engines/17")   // swap for any critical engine _key
LET ac  = FIRST(FOR a IN 1..1 OUTBOUND eng installedOn RETURN a)
LET degrading_subs = (
  FOR s IN 1..1 INBOUND eng partOf
    FILTER s.name IN eng.driverSubsystems RETURN s.name
)
LET parts = (
  FOR s IN 1..1 INBOUND eng partOf
    FILTER s.name IN eng.driverSubsystems
    FOR p IN 1..1 INBOUND s requiredBy
      RETURN DISTINCT { name: p.name, stock: p.stockLevel, blocking: p.stockLevel == 0 }
)
LET techs = (
  FOR s IN 1..1 INBOUND eng partOf
    FILTER s.name IN eng.driverSubsystems
    FOR t IN 1..1 INBOUND s certifiedFor
      FILTER t.homeBase == ac.base
      RETURN DISTINCT t.name
)
RETURN {
  engine:    eng._key,
  riskBucket: eng.riskBucket,
  base:      ac.base,
  degrading: degrading_subs,
  parts:     parts,
  technicians: techs
}
```

> "Engine to aircraft, aircraft to base, base to technicians, engine subsystems to
> required parts — all in a single traversal. ArangoDB evaluates this as a graph
> walk, not a chain of joins."

---

### Query 4 — Procurement dependency chain

> "Work orders with blocking parts have a dependsOn edge — a maintenance WO depends
> on its procurement WO. Let's see that dependency graph."

```aql
FOR wo IN workOrders
  FILTER wo.generatedByPlanner == true
  FILTER wo.type == "maintenance"
  LET proc = FIRST(FOR d IN 1..1 OUTBOUND wo dependsOn RETURN d)
  FILTER proc != null
  RETURN {
    maintenance:  wo._key,
    engine:       wo.engineId,
    maint_status: wo.status,
    depends_on:   proc._key,
    proc_status:  proc.status,
    proc_deadline: proc.deadline
  }
```

> "Pending-parts maintenance WOs can't start until their procurement WO closes.
> That dependency lives as an edge, so you can traverse it in either direction —
> 'what is this maintenance waiting for?' or 'what maintenance unblocks when
> this procurement closes?'"

---

### Query 5 — Graph Visualizer

Switch to the **Graph** tab in the ArangoDB console. Select **fleetGraph**.

Start from a specific engine document (e.g. `engines/17`). Expand:

1. **OUTBOUND installedOn** → aircraft
2. **INBOUND partOf** → subsystems
3. **INBOUND requiredBy** → parts
4. **INBOUND certifiedFor** → technicians

> "Nine vertex collections, nine edge collections, one named graph. The visualizer
> is reading the same schema the application uses. There's no ETL, no separate
> analytics layer — the operational graph *is* the analytical graph."

---

## Closing line

> "Predict from real sensor data. Understand through the operational graph.
> Ask in natural language. Plan with a constrained AI agent. All on a single
> ArangoDB instance — no data movement, no separate stores, no joins."

---

## Quick recovery prompts

| If... | Say / do... |
|---|---|
| Plan generation takes too long | "It's running — let me show the AQL queries while it works." Switch to Act 6, come back. |
| Chat agent loops or stalls | Refresh the page; plan state is in the database, not the browser. |
| Gina Moore has no work orders | Reset the plan: click "Reset Plan" then "Generate" again before the demo. |
| Google sign-in loops | Clear cookies for the domain; OAuth session may be stale. |
| txt2aql / chat returns an error | "The NL query service is separate infrastructure — let me show the direct AQL equivalent." |
