"""
Deterministic synthetic operational graph for AeroFleet.

Call generate_all() to get every document and edge list needed to populate
the nine operational collections. All randomness is seeded (seed=42) via a
local Random instance; output is identical on every run.

Edge-direction conventions (FROM → TO):
  installedOn  : engines  → aircraft
  partOf       : subsystems → engines
  monitors     : sensors   → subsystems
  requiredBy   : parts     → subsystems   (part required by each matching subsystem)
  certifiedFor : technicians → subsystems (tech certified for subsystems at their base)
  maintains    : workOrders → engines
  performedBy  : workOrders → technicians
  consumed     : workOrders → parts

requiredBy design: each catalogue part is linked to every subsystem instance
of its type, so a traversal from any specific engine subsystem directly reaches
the applicable spare parts.

certifiedFor design: edges connect a technician to the subsystem instances at
their home base only (not fleet-wide), keeping the edge count manageable and
making the impact traversal geographically meaningful.
"""

import random
import string
from datetime import date, timedelta

from faker import Faker

BASES = ["LHR", "JFK", "SIN", "DXB", "FRA"]
BASE_PREFIXES = {"LHR": "G", "JFK": "N", "SIN": "9V", "DXB": "A6", "FRA": "D"}
ENGINE_MODELS = ["CFM56-7B27", "CFM56-5B4", "V2527-A5", "PW4077D"]
SUBSYSTEM_TYPES = ["fan", "LPC", "HPC", "combustor", "HPT", "LPT"]

# See pipeline/MAPPING.md for full rationale
SENSOR_TO_SUBSYSTEM: dict[str, str] = {
    "s1": "fan",       # T2   — fan inlet temperature (near-constant)
    "s2": "LPC",       # T24  — LPC outlet temperature
    "s3": "HPC",       # T30  — HPC outlet temperature
    "s4": "LPT",       # T50  — LPT outlet temperature
    "s5": "fan",       # P2   — fan inlet pressure (near-constant)
    "s6": "fan",       # P15  — bypass-duct pressure (near-constant)
    "s7": "HPC",       # P30  — HPC outlet pressure
    "s8": "fan",       # Nf   — physical fan speed
    "s9": "HPC",       # Nc   — physical core speed
    "s10": "fan",      # epr  — engine pressure ratio (near-constant)
    "s11": "HPC",      # Ps30 — static pressure at HPC outlet
    "s12": "combustor",# phi  — fuel-flow / Ps30
    "s13": "fan",      # NRf  — corrected fan speed
    "s14": "HPC",      # NRc  — corrected core speed
    "s15": "fan",      # BPR  — bypass ratio
    "s16": "combustor",# farB — burner fuel-air ratio (near-constant)
    "s17": "HPC",      # htBleed — bleed enthalpy
    "s18": "fan",      # Nf_dmd  — demanded fan speed (near-constant)
    "s19": "fan",      # PCNfR_dmd — demanded corrected fan speed (near-constant)
    "s20": "HPT",      # W31  — HPT coolant bleed
    "s21": "LPT",      # W32  — LPT coolant bleed
}

# (name, subsystem_type) — ~40 catalogue spare parts
PARTS_CATALOGUE: list[tuple[str, str]] = [
    # Fan (7)
    ("Fan blade set", "fan"),
    ("Fan disk assembly", "fan"),
    ("Fan case acoustic liner", "fan"),
    ("Inlet particle separator", "fan"),
    ("Variable bleed valve actuator", "fan"),
    ("Thrust reverser actuator", "fan"),
    ("Fan inlet cowl panel", "fan"),
    # LPC (5)
    ("LPC blade set", "LPC"),
    ("LPC vane set", "LPC"),
    ("LPC disk", "LPC"),
    ("LPC interstage seal", "LPC"),
    ("Booster stage assembly", "LPC"),
    # HPC (9)
    ("HPC blade set stage 1-4", "HPC"),
    ("HPC vane set", "HPC"),
    ("HPC disk", "HPC"),
    ("HPC interstage seal", "HPC"),
    ("Variable stator vane actuator", "HPC"),
    ("Compressor wash kit", "HPC"),
    ("Anti-ice valve", "HPC"),
    ("Bleed air valve", "HPC"),
    ("HPC diffuser case", "HPC"),
    # Combustor (5)
    ("Combustor liner", "combustor"),
    ("Fuel nozzle set (20x)", "combustor"),
    ("Igniter plug set", "combustor"),
    ("Combustor dome assembly", "combustor"),
    ("Fuel flow divider valve", "combustor"),
    # HPT (6)
    ("HPT blade set stage 1", "HPT"),
    ("HPT nozzle guide vane", "HPT"),
    ("HPT disk", "HPT"),
    ("HPT shroud segment set", "HPT"),
    ("HPT cooling air valve", "HPT"),
    ("HPT interstage seal", "HPT"),
    # LPT (7)
    ("LPT blade set stage 1-4", "LPT"),
    ("LPT vane set", "LPT"),
    ("LPT disk", "LPT"),
    ("LPT interstage seal", "LPT"),
    ("LPT exit case", "LPT"),
    ("LPT rear frame bearing", "LPT"),
    ("LPT exhaust plug", "LPT"),
]

# Indices into PARTS_CATALOGUE that start with zero stock.
# Two items per subsystem type so virtually every degrading engine will have
# at least one blocking part — creating richer procurement + maintenance timelines.
ZERO_STOCK_INDICES: frozenset[int] = frozenset({
    0,   # "Fan blade set"           (fan)
    1,   # "Fan disk assembly"       (fan)
    7,   # "LPC blade set"           (LPC)
    8,   # "LPC vane set"            (LPC)
    12,  # "HPC blade set stage 1-4" (HPC)
    14,  # "HPC disk"                (HPC)
    21,  # "Combustor liner"         (combustor)
    22,  # "Fuel nozzle set (20x)"   (combustor)
    26,  # "HPT blade set stage 1"   (HPT)
    27,  # "HPT nozzle guide vane"   (HPT)
    32,  # "LPT blade set stage 1-4" (LPT)
    34,  # "LPT disk"                (LPT)
})

_WORK_ORDER_DESCRIPTIONS = [
    "Routine scheduled maintenance (C-check)",
    "Unscheduled inspection — vibration anomaly",
    "Component replacement — wear limit exceeded",
    "Post-flight inspection and repair",
    "Hot section inspection (HSI)",
    "Borescope inspection",
    "Line maintenance — FOD check",
    "Engine water wash and performance recovery",
    "Performance restoration shop visit",
    "On-wing repair — minor erosion",
]


def generate_all() -> dict[str, list[dict]]:
    """Return all documents and edges for every operational collection.

    The returned dict maps collection name → list of documents/edges.
    Call this exactly once from the loader — reseed happens internally.
    """
    rng = random.Random(42)
    Faker.seed(42)
    fake = Faker()

    engines = _gen_engines(rng, fake)
    aircraft, installed_on = _gen_aircraft(engines, rng)

    # engine_base is used by _gen_technicians to scope certifiedFor edges
    ac_by_key = {ac["_key"]: ac for ac in aircraft}
    engine_base: dict[str, str] = {
        edge["_from"].split("/")[1]: ac_by_key[edge["_to"].split("/")[1]]["base"]
        for edge in installed_on
    }

    subsystems, part_of = _gen_subsystems(engines)
    sensors, monitors = _gen_sensors(engines, subsystems)
    parts, required_by = _gen_parts(subsystems, rng)
    technicians, certified_for = _gen_technicians(aircraft, subsystems, engine_base, rng, fake)
    work_orders, maintains, performed_by, consumed = _gen_work_orders(
        engines, technicians, parts, rng, fake
    )

    return {
        "engines": engines,
        "aircraft": aircraft,
        "subsystems": subsystems,
        "sensors": sensors,
        "parts": parts,
        "technicians": technicians,
        "workOrders": work_orders,
        "installedOn": installed_on,
        "partOf": part_of,
        "monitors": monitors,
        "requiredBy": required_by,
        "certifiedFor": certified_for,
        "maintains": maintains,
        "performedBy": performed_by,
        "consumed": consumed,
    }


# ---------------------------------------------------------------------------
# Individual generators
# ---------------------------------------------------------------------------

def _gen_engines(rng: random.Random, fake: Faker) -> list[dict]:
    base_date = date(2008, 1, 1)
    return [
        {
            "_key": str(i),
            "engineId": i,
            "model": rng.choice(ENGINE_MODELS),
            "entryIntoService": (
                base_date + timedelta(days=rng.randint(0, 365 * 14))
            ).isoformat(),
        }
        for i in range(1, 101)
    ]


def _gen_aircraft(
    engines: list[dict], rng: random.Random
) -> tuple[list[dict], list[dict]]:
    engine_keys = [e["_key"] for e in engines]
    rng.shuffle(engine_keys)

    aircraft: list[dict] = []
    edges: list[dict] = []
    for i, (e1, e2) in enumerate(zip(engine_keys[::2], engine_keys[1::2])):
        base = BASES[i % len(BASES)]
        suffix = "".join(rng.choices(string.ascii_uppercase, k=4))
        tail = f"{BASE_PREFIXES[base]}-{suffix}"
        ac_key = f"AC{i + 1:03d}"
        fpd = rng.randint(1, 3)
        aircraft.append({"_key": ac_key, "tailNumber": tail, "base": base, "flightsPerDay": fpd})
        edges.append({"_from": f"engines/{e1}", "_to": f"aircraft/{ac_key}"})
        edges.append({"_from": f"engines/{e2}", "_to": f"aircraft/{ac_key}"})
    return aircraft, edges


def _gen_subsystems(engines: list[dict]) -> tuple[list[dict], list[dict]]:
    docs: list[dict] = []
    edges: list[dict] = []
    for e in engines:
        eid = e["_key"]
        for stype in SUBSYSTEM_TYPES:
            key = f"{eid}_{stype}"
            docs.append({"_key": key, "name": stype, "engineId": int(eid)})
            edges.append({"_from": f"subsystems/{key}", "_to": f"engines/{eid}"})
    return docs, edges


def _gen_sensors(
    engines: list[dict], subsystems: list[dict]
) -> tuple[list[dict], list[dict]]:
    sub_idx: dict[tuple[str, str], str] = {
        (str(s["engineId"]), s["name"]): s["_key"] for s in subsystems
    }
    docs: list[dict] = []
    edges: list[dict] = []
    for e in engines:
        eid = e["_key"]
        for n in range(1, 22):
            sname = f"s{n}"
            sub_key = sub_idx[(eid, SENSOR_TO_SUBSYSTEM[sname])]
            key = f"{eid}_{sname}"
            docs.append({"_key": key, "sensorId": sname, "engineId": int(eid)})
            edges.append({"_from": f"sensors/{key}", "_to": f"subsystems/{sub_key}"})
    return docs, edges


def _gen_parts(
    subsystems: list[dict], rng: random.Random
) -> tuple[list[dict], list[dict]]:
    sub_by_type: dict[str, list[str]] = {}
    for s in subsystems:
        sub_by_type.setdefault(s["name"], []).append(s["_key"])

    docs: list[dict] = []
    parts_by_type: dict[str, list[str]] = {}
    for idx, (name, sub_type) in enumerate(PARTS_CATALOGUE):
        key = f"P{idx + 1:03d}"
        stock = 0 if idx in ZERO_STOCK_INDICES else rng.randint(1, 20)
        # Heavy parts (blade sets, disks) take 5-10 days; consumables 1-4 days.
        lead = rng.randint(5, 10) if idx in ZERO_STOCK_INDICES else rng.randint(1, 4)
        docs.append({
            "_key": key,
            "name": name,
            "subsystemType": sub_type,
            "stockLevel": stock,
            "leadTimeDays": lead,
        })
        parts_by_type.setdefault(sub_type, []).append(key)

    # Each subsystem INSTANCE is linked to a random 2-4 catalogue parts for its
    # type rather than all of them.  With 2 zero-stock items per type this gives
    # roughly a 40-60 % chance that an instance's required parts are all in stock,
    # creating a natural mix of direct-maintenance and procurement-first engines.
    edges: list[dict] = []
    for sub_type, sub_keys in sub_by_type.items():
        catalogue = parts_by_type.get(sub_type, [])
        if not catalogue:
            continue
        k_max = min(4, len(catalogue))
        for sub_key in sub_keys:
            k = rng.randint(2, k_max)
            for part_key in rng.sample(catalogue, k=k):
                edges.append({"_from": f"parts/{part_key}", "_to": f"subsystems/{sub_key}"})

    return docs, edges


def _gen_technicians(
    aircraft: list[dict],
    subsystems: list[dict],
    engine_base: dict[str, str],
    rng: random.Random,
    fake: Faker,
) -> tuple[list[dict], list[dict]]:
    # Index subsystem keys by (base, subsystem_type)
    sub_by_base_type: dict[tuple[str, str], list[str]] = {}
    for s in subsystems:
        base = engine_base.get(str(s["engineId"]))
        if base:
            sub_by_base_type.setdefault((base, s["name"]), []).append(s["_key"])

    docs: list[dict] = []
    edges: list[dict] = []
    for i in range(10):   # 2 technicians per base
        base = BASES[i % len(BASES)]
        # Broad certifications (4-5 out of 6 subsystem types) so each tech can
        # cover most at-risk engines at their base, keeping workloads dense.
        certs = rng.sample(SUBSYSTEM_TYPES, rng.randint(4, 5))
        key = f"T{i + 1:03d}"
        docs.append({
            "_key": key,
            "name": fake.name(),
            "homeBase": base,
            "certifications": certs,
        })
        for cert in certs:
            for sub_key in sub_by_base_type.get((base, cert), []):
                edges.append({"_from": f"technicians/{key}", "_to": f"subsystems/{sub_key}"})
    return docs, edges


def _gen_work_orders(
    engines: list[dict],
    technicians: list[dict],
    parts: list[dict],
    rng: random.Random,
    fake: Faker,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    # Fixed reference date so work order dates don't drift between runs
    today = date(2026, 7, 6)

    docs: list[dict] = []
    maintains: list[dict] = []
    performed_by: list[dict] = []
    consumed: list[dict] = []

    for i in range(200):
        key = f"WO{i + 1:04d}"
        engine = rng.choice(engines)
        tech = rng.choice(technicians)
        n_parts = rng.randint(1, 3)
        wo_parts = rng.sample(parts, n_parts)
        wo_date = (today - timedelta(days=rng.randint(1, 365 * 5))).isoformat()

        docs.append({
            "_key": key,
            "date": wo_date,
            "description": rng.choice(_WORK_ORDER_DESCRIPTIONS),
            "status": "closed",
        })
        maintains.append({"_from": f"workOrders/{key}", "_to": f"engines/{engine['_key']}"})
        performed_by.append({"_from": f"workOrders/{key}", "_to": f"technicians/{tech['_key']}"})
        for part in wo_parts:
            consumed.append({"_from": f"workOrders/{key}", "_to": f"parts/{part['_key']}"})

    return docs, maintains, performed_by, consumed
