# C-MAPSS FD001 Sensor → Subsystem Mapping

Each of the 21 C-MAPSS sensors is assigned to one of the six turbofan
subsystems modelled in the demo graph. The mapping follows the physical station
meanings documented in the C-MAPSS readme; sensors whose channel is constant or
near-constant in FD001 are noted — they are excluded from the scorer's
`DRIFT_SENSORS` list but still represented as graph nodes.

| Sensor | C-MAPSS channel | Physical meaning                   | Subsystem  | Drifts? |
|--------|-----------------|------------------------------------|------------|---------|
| s1     | T2              | Total temperature — fan inlet      | fan        | no      |
| s2     | T24             | Total temperature — LPC outlet     | LPC        | yes     |
| s3     | T30             | Total temperature — HPC outlet     | HPC        | yes     |
| s4     | T50             | Total temperature — LPT outlet     | LPT        | yes     |
| s5     | P2              | Pressure — fan inlet               | fan        | no      |
| s6     | P15             | Total pressure — bypass duct       | fan        | no      |
| s7     | P30             | Total pressure — HPC outlet        | HPC        | yes     |
| s8     | Nf              | Physical fan speed                 | fan        | yes     |
| s9     | Nc              | Physical core speed                | HPC        | yes     |
| s10    | epr             | Engine pressure ratio              | fan        | no      |
| s11    | Ps30            | Static pressure — HPC outlet       | HPC        | yes     |
| s12    | phi             | Fuel-flow / Ps30 ratio             | combustor  | yes     |
| s13    | NRf             | Corrected fan speed                | fan        | yes     |
| s14    | NRc             | Corrected core speed               | HPC        | yes     |
| s15    | BPR             | Bypass ratio                       | fan        | yes     |
| s16    | farB            | Burner fuel-air ratio              | combustor  | no      |
| s17    | htBleed         | Bleed enthalpy                     | HPC        | yes     |
| s18    | Nf_dmd          | Demanded fan speed                 | fan        | no      |
| s19    | PCNfR_dmd       | Demanded corrected fan speed       | fan        | no      |
| s20    | W31             | HPT coolant bleed                  | HPT        | yes     |
| s21    | W32             | LPT coolant bleed                  | LPT        | yes     |

## Placement rationale for non-explicitly-documented sensors

| Sensor | Rationale |
|--------|-----------|
| s1 / T2   | Fan inlet total temperature — owned by the fan subsystem. Near-constant in FD001 (constant ambient). |
| s5 / P2   | Fan inlet pressure — fan. Near-constant (no altitude variation in FD001). |
| s6 / P15  | Bypass duct pressure — downstream of the fan, owned by fan. Near-constant. |
| s10 / epr | Engine pressure ratio = P56/P1.8, an overall fan performance metric. Assigned to fan. Near-constant. |
| s13 / NRf | Corrected fan speed — directly characterises fan degradation. |
| s15 / BPR | Bypass ratio — set by the fan and bypass nozzle area. Assigned to fan. |
| s16 / farB| Burner fuel-air ratio — combustor. Near-constant in FD001. |
| s18 / Nf_dmd | Demanded (commanded) fan speed — a flight-deck signal routed to the fan controller. Assigned to fan. Near-constant. |
| s19 / PCNfR_dmd | Demanded corrected fan speed — same rationale. Near-constant. |
