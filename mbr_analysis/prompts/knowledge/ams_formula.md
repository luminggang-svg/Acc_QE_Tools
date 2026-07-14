# AMS Formula Reference

AMS = (Coverage × 0.40) + (Reliability × 0.30) + (Efficiency × 0.30)

## Coverage (40% of AMS)
Coverage = (Backend_Coverage × 0.60) + (Mobile_Coverage × 0.20) + (Web_Coverage × 0.20)

Backend_Coverage = (Unit × 0.35) + (Contract × 0.35) + (Intra_Service × 0.10) + (Inter_Service × 0.15) + (API_E2E × 0.05)
Mobile_Coverage  = (Unit × 0.30) + (Integration × 0.20) + (E2E × 0.50)
Web_Coverage     = (Unit × 0.30) + (Component × 0.20) + (E2E × 0.50)

## Reliability (30% of AMS)
Reliability = (Backend_Stability × 0.50) + (Mobile_Stability × 0.25) + (Web_Stability × 0.25)
Stability per test case: stable if (successes / total executions) >= 80%

## Efficiency (30% of AMS)
Efficiency = (1 - Current_Manual_Hours / Baseline_Hours) × 100
Tiers: ≤50h = Optimized (95pts), 51-100h = Advanced (75-94pts), 101-150h = Developing (25-74pts), >150h = Initial

## Maturity Levels
- Level 5 Optimizing: 81-100
- Level 4 Measured: 61-80
- Level 3 Defined: 41-60
- Level 2 Emerging: 21-40
- Level 1 Initial: 0-20
