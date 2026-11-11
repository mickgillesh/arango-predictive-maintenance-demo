# Demo Questions and Answer Key

These questions drive notebook sections 6-8. Each one lists the facts a good answer **must** contain. They are drawn from the corpus documents and, for cross-graph questions, from the structured fleet graph.

Fleet facts below assume the default deterministic load (`load → score → embed`, no `FORCED_CRITICAL`). Notebook section 2 prints the critical engines so you can confirm them before the session.

| Fleet fact (default load) | Value |
|---|---|
| Critical engines | 48 (V2527-A5, HPC + combustor, aircraft D-XFGC, FRA), 9 (CFM56-7B27, HPC, N-HITB, JFK), 51 (PW4077D, HPC + fan, D-APIU, FRA) |
| CFM56-7B27 engines in critical or warning tier with HPC as a driver subsystem | 4, 5, 7, 9, 18, 28, 41, 55, 58, 64, 69, 74, 78, 89, 95, 96 (16 engines) |
| Blocking (zero-stock) parts for the critical engines | 9: HPC blade set stage 1-4 · 48: Fuel nozzle set (20x), Combustor liner · 51: HPC blade set stage 1-4, Fan disk assembly, Fan blade set |

---

## A. Document-only questions (AutoRAG `ask`, section 6)

### A1 — Local: a specific directive
**Question:** What does AD-EASA-2024-0312 require for CFM56-7B27 HPC blades?
**Mode:** LOCAL
**Must contain:**
- Borescope inspection of HPC stages 3, 4 and 5 every 100 cycles
- Reject criteria: tip curl > 0.8 mm, leading edge erosion > 1.2 mm, any crack
- Stage 5 tip clearance below 0.6 mm requires corrective action
- Cause: accelerated erosion in high-dust environments (Middle East / North Africa)

### A2 — Local: diagnostic reasoning across documents
**Question:** How can we tell HPC fouling from HPC erosion on a CFM56 engine?
**Mode:** LOCAL or UNIFIED
**Must contain:**
- A compressor water wash recovers fouling but not erosion (SB-CFM-56-72-0193)
- EGT margin recovery of less than 2°C after a wash points to erosion or tip clearance loss
- Follow-up: HPC borescope within 50 cycles (AMM-CFM56-7B-72-00-00-200-801)
- Trend indicators: T30 rise, EGT margin loss

### A3 — Local: model + subsystem lookup
**Question:** Which documents apply to the V2527-A5 combustor, and how often must it be inspected?
**Mode:** LOCAL
**Must contain:**
- AD-FAA-2024-0583, every 200 cycles (350 cycles with an approved extended programme)
- AMM-V2500-72-41-00-200-801 is the accomplishment task
- Fuel injectors cleaned or replaced at 1,500 cycles since cleaning
- Liner crack reject limit: > 25 mm or > 50% through thickness

### A4 — Unified: sensor-to-subsystem knowledge
**Question:** Which sensor indicates HPT cooling problems, and which tasks or directives use it?
**Mode:** UNIFIED
**Must contain:**
- W31 (HPT coolant bleed)
- SB-IAE-V2500-A5-72-0087 / AMM-V2500-72-51-00-200-801 (V2527-A5 TBC loss)
- AD-FAA-2023-1889 / AMM-PW4000-72-51-00-200-801 (PW4077D shroud wear)

### A5 — Global: themes
**Question:** What are the main deterioration mechanisms across our maintenance documentation?
**Mode:** GLOBAL
**Must contain (at least three):**
- Particulate erosion of compressor blades (HPC, LPC, fan)
- Thermal fatigue and coating loss in hot sections (combustor, HPT, LPT)
- Seal wear and clearance growth (HPC labyrinth seals, HPT shroud, LPT tip seals)
- Fuel nozzle coking
- The role of engine condition trend monitoring in triggering inspections

### A6 — Distractor check
**Question:** Does AD-EASA-2023-0891 apply to any engine in our fleet?
**Mode:** LOCAL
**Must contain:**
- No — it applies to Rolls-Royce Trent 700 engines only (LPT stage 1 blades)
- The fleet engine models are CFM56-7B27, CFM56-5B4, V2527-A5 and PW4077D

---

## B. Cross-graph questions (custom retriever tools and Deep Search, section 7)

### B1 — Fleet status (tool `aerofleet_fleet_status_v5`)
**Question:** Which engines are critical and what is blocking their maintenance?
**Must contain:**
- Engines 48 (D-XFGC, FRA), 9 (N-HITB, JFK) and 51 (D-APIU, FRA)
- Degrading subsystems per engine
- Blocking parts: HPC blade set stage 1-4 (engines 9 and 51); Fuel nozzle set (20x) and Combustor liner (48); Fan disk assembly and Fan blade set (51)
- Certified technicians at the aircraft's base

### B2 — Deep Search across both graphs
**Question:** For our critical engines, which airworthiness directives and maintenance tasks apply to their degrading subsystems, and are the required parts in stock?
**Must contain:**
- Engine 9 (CFM56-7B27, HPC): AD-EASA-2024-0312, SB-CFM-56-72-0193, AMM-CFM56-7B-72-00-00-200-801
- Engine 48 (V2527-A5, HPC + combustor): AD-EASA-2023-1423, AD-FAA-2024-0583, AMM-V2500-72-00-00-200-802, AMM-V2500-72-41-00-200-801
- Engine 51 (PW4077D, HPC + fan): AD-EASA-2023-1423, AMM-PW4000-72-00-00-200-801, AMM-PW4000-72-21-00-200-801
- Blocking parts as in B1 — notably HPC blade set stage 1-4, which the AMM and AD documents also name

---

## C. Incremental update (section 8)

The held-back document `corpus/incremental/AD-EASA-2026-0214.md` is an emergency AD that supersedes AD-EASA-2024-0312.

### C1 — Before / after (UNIFIED)
**Question:** What are the current mandatory HPC inspection requirements for CFM56-7B27 engines?

| | Before incremental update | After incremental update |
|---|---|---|
| Governing directive | AD-EASA-2024-0312 | AD-EASA-2026-0214 (supersedes AD-EASA-2024-0312) |
| Stages inspected | 3-5 | 1-5 |
| Interval | 100 cycles (200 with approved programme) | 50 cycles, no extension; within 10 cycles for engines with an active HPC trend alert or in the critical/warning tier with HPC as a driver subsystem |
| Erosion reject limit | 1.2 mm (stages 3-5) | 0.6 mm (stages 1-2), 0.7 mm (stages 3-5) |
| On reject | Remove engine; blade replacement | Replace the complete HPC blade set stage 1-4 — no single-blade replacement |
| Wash | Recommended (SB-CFM-56-72-0193) | Mandatory after every inspection |

### C2 — Deep Search after the update
**Question:** Which CFM56-7B27 engines must be inspected within 10 cycles under AD-EASA-2026-0214, and can we replace the HPC blade set stage 1-4 if they fail?
**Must contain:**
- The CFM56-7B27 engines in the critical or warning tier with HPC as a driver subsystem (engine 9 is critical; 16 engines in total with default data)
- HPC blade set stage 1-4 has zero stock — a blocking part
- Contact CFM Customer Support to reserve pooled blade sets (from the AD)
