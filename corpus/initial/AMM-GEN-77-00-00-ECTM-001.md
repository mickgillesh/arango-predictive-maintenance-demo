# AMM-GEN-77-00-00-ECTM-001 — Engine Condition Trend Monitoring and Fault Isolation

**Document Type:** Engine Maintenance Manual Task  
**Publisher:** Operator Powerplant Engineering (applies CFM International, IAE and Pratt & Whitney ECTM guidance)  
**Effective Date:** 2024-07-01  
**Affected Engine Models:** CFM56-7B27, CFM56-5B4, V2527-A5, PW4077D  
**Affected Subsystems:** fan, LPC, HPC, combustor, HPT, LPT  
**Task Interval:** Continuous — daily review of trend data; weekly fleet review  
**Supersedes:** None  
**Related Documents:** AD-EASA-2024-0312, SB-CFM-56-72-0193, AMM-CFM56-7B-72-00-00-200-801, AMM-CFM56-72-00-00-100-801, AMM-CFM56-72-21-00-200-801, AMM-CFM56-5B-72-22-00-200-801, AMM-CFM56-72-41-00-200-801, AMM-CFM56-72-54-00-200-801, AMM-V2500-72-00-00-200-802, AMM-V2500-72-41-00-200-801, AMM-V2500-72-51-00-200-801, AMM-V2500-72-54-00-200-801, AMM-PW4000-72-00-00-200-801, AMM-PW4000-72-31-00-200-801, AMM-PW4000-72-41-00-200-801, AMM-PW4000-72-21-00-200-801, AMM-PW4000-72-51-00-200-801

## Task Summary

This task defines how the operator monitors the health of its turbofan fleet from in-flight and take-off sensor data, how trend shifts are attributed to an engine subsystem, and which maintenance task is triggered for each signature. It applies to the four engine types in the fleet: CFM56-7B27 (Boeing 737-800), CFM56-5B4 (Airbus A320), V2527-A5 (Airbus A320) and PW4077D (Boeing 777-200). The task links trend monitoring to the subsystem inspection tasks listed in Related Documents and to the airworthiness directives whose alternative methods of compliance require continuous trend monitoring, such as AD-EASA-2024-0312.

| Item | Value |
|---|---|
| Responsible function | Powerplant Engineering, Engine Health Monitoring desk |
| Data sources | ACARS take-off and cruise reports, quick access recorder downloads |
| Review frequency | Daily for alerts, weekly for fleet ranking |
| Personnel | Powerplant engineer, trend monitoring analyst |

## Sensor-to-Subsystem Map

Every monitored parameter is assigned to the subsystem whose condition it most directly reflects. The station numbers follow the common turbofan convention used in the NASA C-MAPSS engine model and in OEM trend monitoring software.

| Sensor | Parameter | Physical Meaning | Subsystem |
|---|---|---|---|
| s1 | T2 | Fan inlet total temperature | fan |
| s2 | T24 | LPC (booster) outlet total temperature | LPC |
| s3 | T30 | HPC outlet total temperature | HPC |
| s4 | T50 | LPT outlet total temperature (EGT proxy) | LPT |
| s5 | P2 | Fan inlet pressure | fan |
| s6 | P15 | Bypass duct total pressure | fan |
| s7 | P30 | HPC outlet total pressure | HPC |
| s8 | Nf | Physical fan speed (N1) | fan |
| s9 | Nc | Physical core speed (N2) | HPC |
| s10 | epr | Engine pressure ratio | fan |
| s11 | Ps30 | HPC outlet static pressure | HPC |
| s12 | phi | Fuel flow to Ps30 ratio | combustor |
| s13 | NRf | Corrected fan speed | fan |
| s14 | NRc | Corrected core speed | HPC |
| s15 | BPR | Bypass ratio | fan |
| s16 | farB | Burner fuel-air ratio | combustor |
| s17 | htBleed | Bleed enthalpy | HPC |
| s18 | Nf_dmd | Demanded fan speed | fan |
| s19 | PCNfR_dmd | Demanded corrected fan speed | fan |
| s20 | W31 | HPT coolant bleed | HPT |
| s21 | W32 | LPT coolant bleed | LPT |

Parameters s1, s5, s6, s10, s16, s18 and s19 are essentially constant at a given flight condition and are used for data validation rather than for trending.

## Health Index and Risk Classification

Each engine receives a health index between 0.0 (new) and 1.0 (end of serviceable life) derived from the drift of the trended sensors against the engine's own post-shop-visit baseline. The health index is converted to a predicted remaining useful life (RUL) in flight cycles, and each engine is placed in a risk bucket:

| Risk Bucket | Criterion | Required Response |
|---|---|---|
| critical | Lowest RUL in the fleet; drift beyond 2σ on two or more driver sensors | Engineering review within 24 hours; targeted inspection before next revenue flight where an AD or AMM task applies |
| warning | Intermediate RUL; drift beyond 1.5σ on one or more driver sensors | Targeted inspection within 50 cycles; plan parts and technicians |
| healthy | RUL above threshold | Routine programme only |

The sensors contributing most to the drift are recorded as the engine's driver sensors and are mapped through the table above to driver subsystems. The driver subsystems decide which inspection task is raised.

## Fault Isolation Matrix

| Signature | Primary Subsystem | First Action | Model-Specific Task |
|---|---|---|---|
| T30 and EGT rising, Ps30 falling at constant N2, recovers after wash | HPC (fouling) | Compressor wash | CFM56: AMM-CFM56-72-00-00-100-801 |
| T30 and EGT rising, Nc/NRc rising, no recovery after wash | HPC (erosion, tip clearance, seals) | HPC borescope | CFM56-7B27: AMM-CFM56-7B-72-00-00-200-801; V2527-A5: AMM-V2500-72-00-00-200-802; PW4077D: AMM-PW4000-72-00-00-200-801 |
| htBleed rising with Ps30 falling | HPC (labyrinth seal leakage) | HPC borescope with seal emphasis | V2527-A5 and PW4077D: see AD-EASA-2023-1423 |
| phi rising, EGT spread increasing | combustor (fuel nozzle coking, liner distress) | Combustor borescope | CFM56: AMM-CFM56-72-41-00-200-801; V2527-A5: AMM-V2500-72-41-00-200-801; PW4077D: AMM-PW4000-72-41-00-200-801 |
| W31 rising, EGT rising with stable T30 | HPT (blade or shroud distress, TBC loss) | HPT borescope / tip clearance | V2527-A5: AMM-V2500-72-51-00-200-801; PW4077D: AMM-PW4000-72-51-00-200-801 |
| T50 rising, W32 rising, N1 vibration change | LPT (tip seal wear, blade distress) | LPT borescope | CFM56: AMM-CFM56-72-54-00-200-801; V2527-A5: AMM-V2500-72-54-00-200-801 |
| T24 rising, Nf/NRf rising for the same thrust | LPC (erosion, VSV mis-rigging) | Booster inspection | CFM56-5B4: AMM-CFM56-5B-72-22-00-200-801; PW4077D: AMM-PW4000-72-31-00-200-801 |
| Nf and BPR shift, N1 vibration rising | fan (blade damage, dovetail wear) | Fan blade inspection | CFM56: AMM-CFM56-72-21-00-200-801; PW4077D: AMM-PW4000-72-21-00-200-801 |

## Procedure

1. Daily, review the automated alert list. An alert is raised when any trended parameter shifts by more than its alert threshold (for example T30 by 5°C or EGT margin by 8°C) between consecutive smoothed points.
2. For each alert, validate the data: check that the non-trended parameters (T2, P2, P15, epr) are consistent with the reported flight condition. Reject the point if they are not.
3. Identify the driver sensors and use the sensor-to-subsystem map to identify the driver subsystems.
4. Use the fault isolation matrix to select the first action and the model-specific task.
5. Check whether an airworthiness directive or service bulletin applies to the subsystem and engine model. Where an AD inspection is due within 100 cycles, combine it with the trend-driven inspection.
6. Raise the work order. Include the engine serial number, the driver subsystems, the AD or SB reference and the task reference.
7. Weekly, rank the fleet by predicted RUL. Present the critical and warning engines with their driver subsystems to maintenance planning so that parts (in particular zero-stock or long-lead items) and certified technicians at the aircraft's home base can be arranged.

## Alert Thresholds

| Parameter | Smoothing | Alert Threshold | Persistence |
|---|---|---|---|
| EGT margin | 10-point exponential | 8°C loss from baseline | 3 consecutive points |
| T30 | 10-point exponential | +5°C | 3 points |
| T24 | 10-point exponential | +3°C | 3 points |
| T50 | 10-point exponential | +6°C | 3 points |
| Nc (N2) | 10-point exponential | +0.6% | 3 points |
| phi (fuel flow ratio) | 10-point exponential | +1.5% | 3 points |
| W31 / W32 | 10-point exponential | +2% | 5 points |
| N1 vibration | Raw | 2.5 units | 2 points |

## Model Notes

- **CFM56-7B27:** The HPC dominates deterioration. Stage 3-5 erosion in high-dust operation is covered by AD-EASA-2024-0312. Combustor liner cracking is covered by AD-FAA-2024-0583.
- **CFM56-5B4:** HPC fouling and LPC (booster) erosion are the main drivers. LPC erosion is covered by AD-EASA-2024-1167.
- **V2527-A5:** HPC labyrinth seal wear (AD-EASA-2023-1423), HPC platform erosion (SB-IAE-V2500-A5-72-0103), HPT TBC distress (SB-IAE-V2500-A5-72-0087) and combustor cracking (AD-FAA-2024-0583).
- **PW4077D:** HPC seal wear (AD-EASA-2023-1423), HPT shroud wear (AD-FAA-2023-1889) and LPC VSV rigging (SB-PW-4000-72-0176).

## Records

Keep the trend data for the life of the engine. Record every alert, the validation result, the driver subsystems, the action raised and the outcome of the inspection. Feed the inspection outcome back to refine the alert thresholds: an alert that leads to an inspection with no finding is a false positive; a finding without a prior alert is a missed detection.

## Background

Engine condition trend monitoring turns routine flight data into early warnings. A turbofan deteriorates gradually: airfoils foul and erode, seals wear open, coatings spall. Each of these changes shifts the engine's operating point in a characteristic way, and because the sensors are positioned between the major modules, the pattern of shifts points to the module responsible. A rise in HPC exit temperature at constant thrust, for example, means the HPC is doing the same work less efficiently; a rise in fuel flow ratio without a change in compressor temperatures points to the combustor; a rise in LPT exit temperature with a change in LPT cooling flow points to the low pressure turbine.

The approach is deliberately conservative: trend monitoring raises the inspection, the inspection finds the damage, and the airworthiness directives and service bulletins define what to do about it. Monitoring never replaces a mandatory inspection, but several directives, including AD-EASA-2024-0312 and AD-FAA-2024-0583, allow extended intervals for operators who run a qualifying monitoring programme.
