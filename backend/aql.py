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

# Engines in one risk bucket with their aircraft (notebook §2 and resume cell).
Q_ENGINES_BY_RISK = """
FOR e IN engines
  FILTER e.riskBucket == @risk
  LET ac = FIRST(FOR a IN 1..1 OUTBOUND e installedOn RETURN a)
  SORT e.predictedRUL
  RETURN {engine: e._key, model: e.model, rul: e.predictedRUL,
          driverSubsystems: e.driverSubsystems, tail: ac.tailNumber, base: ac.base}
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
# Technician-note embeddings (pipeline/embed_runner.py)
# ---------------------------------------------------------------------------

# Engines whose notesEmbedding has the expected length. Bind params: @dimension
Q_EMBED_COUNT_EMBEDDED = """
FOR e IN engines
  FILTER HAS(e, 'notesEmbedding') AND LENGTH(e.notesEmbedding) == @dimension
  COLLECT WITH COUNT INTO n
  RETURN n
"""

# Length of the embeddings in any collection (e.g. AutoGraph chunks), to match
# the note embeddings to the retriever. Bind params: @@coll, @field
Q_EMBEDDING_DIMENSION = """
FOR d IN @@coll
  FILTER IS_ARRAY(d[@field])
  LIMIT 1
  RETURN LENGTH(d[@field])
"""

# SORT is required for determinism: embed_runner consumes a seeded RNG in this
# order, so an unordered scan would assign different notes on different runs.
Q_EMBED_ENGINES_FOR_NOTES = """
FOR e IN engines
  SORT TO_NUMBER(e._key)
  RETURN KEEP(e, ['_key', 'model', 'entryIntoService',
                  'healthIndex', 'riskBucket', 'driverSensors', 'driverSubsystems'])
"""

# ---------------------------------------------------------------------------
# Loader fallback when the platform forbids dropping the named graph.
# Bind params: @@coll (collection name)
# ---------------------------------------------------------------------------

Q_CLEAR_COLLECTION = "FOR doc IN @@coll REMOVE doc IN @@coll"

# ---------------------------------------------------------------------------
# Custom Retriever tool templates (AI Suite GraphRAG Retriever, query_type 4)
#
# These are NOT run by this app. pipeline/retriever_tools.py stores them in the
# Retriever's Tools collection; the Retriever runs them in stage 2 ("AQL graph
# expansion") of its custom-retriever pipeline:
#   1. search  — lexical / semantic / hybrid search over the tool's collection
#   2. expand  — this AQL, with @nodes = the _ids found in stage 1
#   3. cite    — the Retriever formats the returned rows (and citations)
# The Retriever ALWAYS binds @nodes, @@relations_collection (from
# aql_config.relations_collection) and @top_k (from config.top_k), so every
# template must reference all three: ArangoDB rejects a bind parameter the query
# does not declare (ERR 1552). When the AQL fails, the Retriever silently falls
# back to the plain search hits; nothing appears in failed_tools (verified on the
# platform, 2026-11-11). Every other bind parameter has a default in the tool's
# config.aql_config.bind_params.
# ---------------------------------------------------------------------------

# Unstructured docs: AutoGraph chunk -> PART_OF -> source document.
Q_TOOL_TECHDOCS = """
LET nodes = @nodes
FOR nodeId IN nodes
  LIMIT @top_k
  LET chunk = DOCUMENT(nodeId)
  FILTER chunk != null
  LET source_doc = FIRST(
    FOR e IN @@relations_collection
      FILTER e._from == chunk._id AND e.type == "PART_OF"
      LET doc = DOCUMENT(e._to)
      FILTER doc != null
      RETURN doc
  )
  RETURN {
    content:  chunk.content,
    title:    source_doc.title,
    filename: source_doc.filename,
    doc_meta: source_doc
  }
"""

# AutoGraph knowledge graph: entity -> MENTIONED_IN chunks, entity -> RELATED_TO
# entities. Shows the generated graph itself rather than only the text chunks.
Q_TOOL_KG_ENTITIES = """
LET nodes = @nodes
FOR n IN nodes
  LIMIT @top_k
  LET entity = DOCUMENT(n)
  FILTER entity != null
  LET entity_chunks = (
    FOR e IN @@relations_collection
      FILTER e._from == entity._id AND e.type == "MENTIONED_IN"
      LIMIT @max_chunks
      LET chunk = DOCUMENT(e._to)
      FILTER chunk != null
      RETURN { content: chunk.content, chunk_id: chunk._key }
  )
  LET related_entities = (
    FOR e IN @@relations_collection
      FILTER e._from == entity._id AND e.type == "RELATED_TO"
      LIMIT @max_related
      LET related = DOCUMENT(e._to)
      FILTER related != null
      RETURN {
        entity_name: related.entity_name,
        description: related.description,
        relationship: e.description
      }
  )
  // Shape follows the documented entity-expander example: node_id, no top-level
  // `content` (rows with one were intermittently dropped in the dry run).
  RETURN {
    node_id:          entity._key,
    entity_name:      entity.entity_name,
    entity_type:      entity.entity_type,
    description:      entity.description,
    chunks:           entity_chunks,
    related_entities: related_entities
  }
"""

# Structured fleet graph: engine -> aircraft, degrading subsystems, required
# parts (with blocking flag), certified technicians at the aircraft's base and
# the most recent historical work orders. Used by both fleet tools; the
# fleet-status tool sets @degraded_only = true to drop healthy engines.
# Edge directions (see Q_ENGINE_IMPACT):
#   installedOn  engine -> aircraft      (OUTBOUND from engine)
#   partOf       subsystem -> engine     (INBOUND  to engine)
#   requiredBy   part -> subsystem       (INBOUND  to subsystem)
#   certifiedFor technician -> subsystem (INBOUND  to subsystem)
#   maintains    workOrder -> engine     (INBOUND  to engine)
Q_TOOL_FLEET_EXPAND = """
LET nodes = @nodes
FOR node IN nodes
  // Accept an _id ("engines/9") or a bare _key ("9").
  LET eng = DOCUMENT(CONTAINS(node, "/") ? node : CONCAT("engines/", node))
  FILTER eng != null
  FILTER NOT @degraded_only OR eng.riskBucket IN ["critical", "warning"]
  SORT eng.predictedRUL ASC
  LIMIT @top_k
  // @@relations_collection is installedOn (the tool's relations_collection).
  LET ac = FIRST(FOR a IN 1..1 OUTBOUND eng @@relations_collection RETURN a)
  LET degradingSubs = (
    FOR sub IN 1..1 INBOUND eng partOf
      FILTER sub.name IN eng.driverSubsystems
      RETURN sub
  )
  LET allParts = (
    FOR sub IN degradingSubs
      FOR part IN 1..1 INBOUND sub requiredBy
        RETURN DISTINCT {
          name:          part.name,
          subsystemType: part.subsystemType,
          stockLevel:    part.stockLevel,
          leadTimeDays:  part.leadTimeDays,
          blocking:      part.stockLevel == 0
        }
  )
  // Blocking (zero-stock) parts first so @max_parts never hides them.
  LET parts = (FOR p IN allParts SORT p.blocking DESC, p.name LIMIT @max_parts RETURN p)
  LET techs = (
    FOR sub IN degradingSubs
      FOR tech IN 1..1 INBOUND sub certifiedFor
        FILTER tech.homeBase == ac.base
        COLLECT name = tech.name, homeBase = tech.homeBase, certs = tech.certifications
        SORT name
        LIMIT @max_techs
        RETURN { name: name, homeBase: homeBase, certifications: certs }
  )
  LET recent_wos = (
    FOR wo IN 1..1 INBOUND eng maintains
      SORT wo.date DESC
      LIMIT @max_wos
      RETURN { key: wo._key, date: wo.date, description: wo.description, status: wo.status }
  )
  // No top-level `content` field: in the dry run (2026-11-11) rows with one were
  // intermittently dropped by the Retriever (retrieved 1/3 vs 3/3 without it;
  // small sample, root cause unconfirmed).
  RETURN {
    engineId:            eng._key,
    model:               eng.model,
    riskBucket:          eng.riskBucket,
    predictedRUL:        eng.predictedRUL,
    healthIndex:         eng.healthIndex,
    driverSubsystems:    eng.driverSubsystems,
    technicianNotes:     eng.technicianNotes,
    aircraft:            { tailNumber: ac.tailNumber, base: ac.base },
    degradingSubsystems: (FOR s IN degradingSubs RETURN s.name),
    blockingParts:       (FOR p IN allParts FILTER p.blocking RETURN p),
    parts:               parts,
    technicians:         techs,
    recentWorkOrders:    recent_wos
  }
"""
