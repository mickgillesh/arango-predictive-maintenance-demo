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
