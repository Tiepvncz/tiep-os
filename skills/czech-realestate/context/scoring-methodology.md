# Scoring Methodology

How the scanner ranks properties for investment potential.

## Composite Score (0-100)

Weighted sum of five components:

| Component | Weight | What it measures |
|-----------|--------|------------------|
| Gross Yield | 30% | Annual rental income / purchase price |
| Price Discount | 25% | Price/m2 vs. local district average |
| Population Trend | 20% | 5-year growth/decline of district |
| Infrastructure | 15% | Nearby transport, shops, schools, health (POI) |
| Listing Quality | 10% | Data completeness (size, energy, GPS, ownership) |

## Yield Scoring
- 8%+ gross yield = score 100 (excellent)
- 3% gross yield = score 0 (poor)
- Linear interpolation between

## Price Discount Scoring
- 20%+ below local average = score 100
- At average = score 50
- 20%+ above average = score 0

## Population Scoring
- +5% growth over 5 years = score 100
- Flat = score 50
- -5% decline = score 0

## Claude Analysis (Top 10)

Top candidates get a deep-dive analysis that acts as:
1. **Investment advocate** - identifies opportunities, growth potential, convergence signals
2. **Devil's advocate** - stress-tests the thesis, identifies hidden risks, challenges assumptions

Verdicts: BUY / WATCH / SKIP

## Data Sources
- **Listings:** sreality.cz (primary), bezrealitky.cz (owner-direct), bazos.cz (supplementary)
- **Rental comps:** sreality.cz rental listings in same district
- **Population:** CZSO (Czech Statistical Office) annual municipality data
- **Infrastructure:** sreality.cz POI data (transport, shops, schools, health facilities)
