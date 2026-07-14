"""
All AQL queries for AeroFleet. Every query is bind-parameterised.
No string interpolation of user input anywhere in this file.

Naming convention:
  Q_<RESOURCE>_<ACTION>  — e.g. Q_FLEET_LIST, Q_ENGINE_BY_ID
"""

# ---------------------------------------------------------------------------
# Fleet overview
# ---------------------------------------------------------------------------

Q_FLEET_LIST = """
LET kpi = MERGE(
  FOR e IN engines
    COLLECT bucket = e.riskBucket WITH COUNT INTO cnt
    RETURN { [bucket]: cnt }
)
LET engine_list = (
  FOR e IN engines
    LET ac = FIRST(FOR a IN 1..1 OUTBOUND e installedOn RETURN a)
    SORT e.predictedRUL ASC
    RETURN {
      id:           e._key,
      tailNumber:   ac.tailNumber,
      base:         ac.base,
      predictedRUL: e.predictedRUL,
      riskBucket:   e.riskBucket
    }
)
RETURN { kpi: kpi, engines: engine_list }
"""

# ---------------------------------------------------------------------------
# Single engine
# ---------------------------------------------------------------------------

Q_ENGINE_BY_ID = """
LET e = DOCUMENT(CONCAT('engines/', @engineId))
FILTER e != null
LET ac = FIRST(FOR a IN 1..1 OUTBOUND e installedOn RETURN a)
RETURN MERGE(
  KEEP(e, ['_key','engineId','model','entryIntoService',
           'healthIndex','predictedRUL','riskScore','riskBucket',
           'driverSensors','driverSubsystems','scoringMethod']),
  { aircraft: { tailNumber: ac.tailNumber, base: ac.base } }
)
"""

# ---------------------------------------------------------------------------
# Sensor readings (cycle series)
# Bind params: @engineId (int), @sensors (list of sensor name strings)
# ---------------------------------------------------------------------------

Q_ENGINE_READINGS = """
FOR r IN readings
  FILTER r.engineId == @engineId
  SORT r.cycle ASC
  RETURN KEEP(r, APPEND(['cycle'], @sensors))
"""

# ---------------------------------------------------------------------------
# Impact traversal
#
# A SINGLE AQL query — demo talking point.
#
# This query walks four edge collections in one shot:
#   1. installedOn  (engine → aircraft)         1 hop  OUTBOUND
#   2. partOf       (subsystem → engine)         1 hop  INBOUND  from engine
#   3. requiredBy   (part → subsystem)           1 hop  INBOUND  from subsystem
#   4. certifiedFor (technician → subsystem)     1 hop  INBOUND  from subsystem
#
# Steps:
#   a. Resolve the engine document and its aircraft via installedOn.
#   b. Find the engine's subsystem instances that are in driverSubsystems.
#   c. From each degrading subsystem traverse INBOUND on requiredBy to
#      collect the spare parts catalogue entries for that subsystem type.
#      Parts with stockLevel == 0 are flagged blocking: true.
#   d. From each degrading subsystem traverse INBOUND on certifiedFor to
#      find technicians whose homeBase matches the aircraft's base airport.
#   e. Return a structured payload with a top-level blockingParts list for
#      fast UI rendering without a second API call.
#
# Bind params: @engineId (string engine _key)
# ---------------------------------------------------------------------------

Q_ENGINE_IMPACT = """
LET eng = DOCUMENT(CONCAT('engines/', @engineId))
FILTER eng != null
LET ac  = FIRST(FOR a IN 1..1 OUTBOUND eng installedOn RETURN a)

LET degradingSubs = (
  FOR sub IN 1..1 INBOUND eng partOf
    FILTER sub.name IN eng.driverSubsystems
    RETURN sub
)

LET parts = (
  FOR sub IN degradingSubs
    FOR part IN 1..1 INBOUND sub requiredBy
      RETURN DISTINCT {
        id:            part._key,
        name:          part.name,
        subsystemType: part.subsystemType,
        stockLevel:    part.stockLevel,
        leadTimeDays:  part.leadTimeDays,
        blocking:      part.stockLevel == 0
      }
)

LET techs = (
  FOR sub IN degradingSubs
    FOR tech IN 1..1 INBOUND sub certifiedFor
      FILTER tech.homeBase == ac.base
      RETURN DISTINCT {
        id:             tech._key,
        name:           tech.name,
        homeBase:       tech.homeBase,
        certifications: tech.certifications
      }
)

RETURN {
  engine: {
    id:               eng._key,
    riskBucket:       eng.riskBucket,
    predictedRUL:     eng.predictedRUL,
    driverSubsystems: eng.driverSubsystems
  },
  aircraft: {
    tailNumber: ac.tailNumber,
    base:       ac.base
  },
  degradingSubsystems: (FOR sub IN degradingSubs RETURN sub.name),
  parts:         parts,
  technicians:   techs,
  blockingParts: (FOR p IN parts FILTER p.blocking == true RETURN p)
}
"""

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

Q_HEALTH_CHECK = "RETURN { db: 'ok', version: VERSION() }"

# ---------------------------------------------------------------------------
# Maintenance planner — read queries
# ---------------------------------------------------------------------------

Q_PLAN_FLEET_CONTEXT = """
FOR e IN engines
  FILTER e.riskBucket IN ["critical", "warning"]
  LET ac = FIRST(FOR a IN 1..1 OUTBOUND e installedOn RETURN a)
  LET subs = (
    FOR sub IN 1..1 INBOUND e partOf
      FILTER sub.name IN e.driverSubsystems
      RETURN sub
  )
  LET parts = (
    FOR sub IN subs
      FOR p IN 1..1 INBOUND sub requiredBy
        RETURN DISTINCT {
          id: p._key, name: p.name, subsystemType: p.subsystemType,
          stockLevel: p.stockLevel, leadTimeDays: p.leadTimeDays,
          blocking: p.stockLevel == 0
        }
  )
  LET techs = (
    FOR sub IN subs
      FOR t IN 1..1 INBOUND sub certifiedFor
        FILTER t.homeBase == ac.base
        RETURN DISTINCT {
          id: t._key, name: t.name, homeBase: t.homeBase,
          certifications: t.certifications
        }
  )
  SORT e.riskBucket ASC, e.predictedRUL ASC
  RETURN {
    id: e._key, riskBucket: e.riskBucket, predictedRUL: e.predictedRUL,
    driverSubsystems: e.driverSubsystems,
    aircraft: { tailNumber: ac.tailNumber, base: ac.base, flightsPerDay: ac.flightsPerDay },
    parts: parts, technicians: techs
  }
"""

Q_PLAN_WORK_ORDERS = """
FOR wo IN workOrders
  FILTER wo.generatedByPlanner == true
  LET eng  = FIRST(FOR e IN 1..1 OUTBOUND wo maintains  RETURN e)
  LET tech = FIRST(FOR t IN 1..1 OUTBOUND wo performedBy RETURN t)
  LET parts = (
    FOR p IN 1..1 OUTBOUND wo consumed
      RETURN {
        id: p._key, name: p.name, subsystemType: p.subsystemType,
        stockLevel: p.stockLevel, leadTimeDays: p.leadTimeDays,
        blocking: p.stockLevel == 0
      }
  )
  SORT wo.deadline ASC
  RETURN MERGE(wo, {
    engine:     { id: eng._key,  riskBucket: eng.riskBucket,  predictedRUL: eng.predictedRUL },
    technician: { id: tech._key, name: tech.name, homeBase: tech.homeBase },
    parts: parts
  })
"""

Q_PLAN_COLLECT_IDS = """
FOR wo IN workOrders FILTER wo.generatedByPlanner == true RETURN wo._id
"""

# Mutating queries — only executed by the reset endpoint, never via the LangChain chain.
Q_PLAN_DELETE_MAINTAINS = (
    "FOR e IN maintains   FILTER e._from IN @ids REMOVE e IN maintains"
)
Q_PLAN_DELETE_PERFORMED = (
    "FOR e IN performedBy FILTER e._from IN @ids REMOVE e IN performedBy"
)
Q_PLAN_DELETE_CONSUMED = (
    "FOR e IN consumed    FILTER e._from IN @ids REMOVE e IN consumed"
)
Q_PLAN_DELETE_WOS = (
    "FOR wo IN workOrders FILTER wo.generatedByPlanner == true REMOVE wo IN workOrders"
)

# ---------------------------------------------------------------------------
# Chat agent — ontology + cascade AQL
# ---------------------------------------------------------------------------

Q_ONTOLOGY_FULL = """
LET nodes = (FOR n IN ontologyNodes RETURN n)
LET edges = (FOR e IN ontologyEdges RETURN e)
RETURN { nodes: nodes, edges: edges }
"""

Q_CHAT_WO_BY_ENGINE = """
FOR wo IN workOrders
  FILTER wo.generatedByPlanner == true AND wo.engineId == @eid
  LET tech = FIRST(FOR t IN 1..1 OUTBOUND wo performedBy RETURN t)
  RETURN MERGE(wo, {technician: tech})
"""

# Cascade delete helpers — bind-parameterised, called from planning._cascade_delete

Q_CASCADE_WO_KEYS_FOR_ENGINES = """
FOR wo IN workOrders FILTER wo.engineId IN @engine_keys RETURN wo._key
"""

Q_CASCADE_ENGINES_FOR_AIRCRAFT = """
FOR e IN 1..1 INBOUND @aircraft_id installedOn RETURN { key: e._key }
"""

# @@coll is bound by "@coll" in bind_vars — e.g. {"@coll": "maintains", "ids": [...]}
Q_CASCADE_DELETE_EDGES_FROM_IDS = (
    "FOR e IN @@coll FILTER e._from IN @ids REMOVE e IN @@coll"
)
Q_CASCADE_DELETE_EDGES_FROM_ID = (
    "FOR e IN @@coll FILTER e._from == @id REMOVE e IN @@coll"
)
Q_CASCADE_DELETE_EDGES_TO_ID = (
    "FOR e IN @@coll FILTER e._to == @id REMOVE e IN @@coll"
)

# For delete_relationship tool
Q_CASCADE_DELETE_RELATIONSHIP = (
    "FOR e IN @@coll FILTER e._from == @from AND e._to == @to REMOVE e IN @@coll"
)

Q_CASCADE_DELETE_PERFORMEDBY_TO_PLANNER = """
FOR e IN performedBy
  FILTER e._to == @id
  LET wo = DOCUMENT(e._from)
  FILTER wo.generatedByPlanner == true
  REMOVE e IN performedBy
"""
