# Tune Calibration — Optimize Personality Adaptation Rates

## Objective
Find the optimal balance between personality adaptation speed and stability.
Too fast = erratic, sycophantic. Too slow = unresponsive to user preferences.
The calibration system has 11 tunable parameters that control this balance.

## Editable Surface
- src/main/personality-calibration.ts (TUNABLE zone only)

## Metric
Calibration accuracy — how closely the personality dimensions match simulated
user preferences after N interactions. Higher is better (maximize).

## Loop
1. Read current TUNABLE values from personality-calibration.ts
2. Simulate a user interaction sequence with known preferences (e.g., "prefers formal")
3. After 20 simulated signals, measure how close each dimension is to the expected value
4. Adjust ONE weight (explicitWeight, implicitWeight, or decayHalfLifeDays)
5. Re-run simulation
6. If calibration is more accurate: commit
7. If less accurate or sycophancy detected: revert

## Constraints
- explicitWeight must stay between 0.02 and 0.15
- implicitWeight must stay between 0.005 and 0.05
- decayHalfLifeDays must stay between 7 and 30
- proactivitySafetyFloor CANNOT go below 0.2 (cLaw safety requirement)
- dimensionFloor CANNOT go below 0.03
- dimensionCeiling CANNOT go above 0.97
- sycophancyStreakThreshold CANNOT go below 5
- sycophancyBiasThreshold CANNOT go below 0.75

## Budget
2 minutes per cycle, 15 cycles

## Circuit Breaker
- Sycophancy detection triggers (streak or bias threshold crossed)
- A dimension hits 0 or 1 (saturation = broken calibration)
- Personality calibration tests fail
