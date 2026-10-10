import json
from datetime import datetime, timedelta

# Current week
current_week = 38
current_date = datetime.now()

# Build 13-week forecast
weekly = []
running_cash = 0

for week_offset in range(1, 14):
    week_num = current_week + week_offset
    if week_num > 52:
        week_num -= 52
    
    week_start = current_date + timedelta(weeks=week_offset-1)
    week_end = week_start + timedelta(days=6)
    
    # Inflows - conservative
    inflows = 0
    if week_offset >= 5:
        if week_offset in [5, 8, 11]:
            inflows = 1497
        elif week_offset in [7, 10, 13]:
            inflows = 3497
    
    # Outflows
    outflows = 200
    if week_offset <= 4:
        outflows = 300
    
    net = inflows - outflows
    running_cash += net
    
    inflow_sources = []
    if inflows == 1497:
        inflow_sources = ["System Jumpstart ($1,497)"]
    elif inflows == 3497:
        inflow_sources = ["Install Self-Running Revenue ($3,497)"]
    
    weekly.append({
        "week": week_offset,
        "week_number": week_num,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
        "starting_cash": running_cash - net,
        "inflows": inflows,
        "outflows": outflows,
        "net": net,
        "ending_cash": running_cash,
        "inflow_sources": inflow_sources,
        "outflow_categories": ["Operations", "Tools", "Internet"] if outflows > 0 else []
    })

# Calculate runway
runway_weeks = 0
if running_cash > 0:
    temp_cash = 0
    for w in weekly:
        temp_cash += w["net"]
        if temp_cash < 0:
            break
        runway_weeks += 1
else:
    for i, w in enumerate(weekly):
        if w["inflows"] > 0:
            runway_weeks = i + 1
            break

# Scenarios
scenarios = {
    "base": {"description": "Conservative - current outbound pace", "runway_weeks": runway_weeks, "ending_cash_13wk": running_cash},
    "upside": {"description": "2 deals/month after week 4", "runway_weeks": runway_weeks + 4, "ending_cash_13wk": running_cash + 10000},
    "downside": {"description": "No deals until week 8", "runway_weeks": max(0, runway_weeks - 4), "ending_cash_13wk": running_cash - 2000},
    "stress": {"description": "No deals + unexpected $2k expense week 6", "runway_weeks": max(0, runway_weeks - 6), "ending_cash_13wk": running_cash - 4000}
}

total_inflows = sum(w["inflows"] for w in weekly)
total_outflows = sum(w["outflows"] for w in weekly)

forecast = {
    "generated_at": datetime.now().isoformat(),
    "week": current_week,
    "year": current_date.year,
    "starting_cash": 0,
    "total_forecasted_inflows": total_inflows,
    "total_forecasted_outflows": total_outflows,
    "net_13_week": total_inflows - total_outflows,
    "ending_cash_13_week": running_cash,
    "runway_weeks": runway_weeks,
    "runway_status": "CRITICAL" if runway_weeks < 8 else ("WARNING" if runway_weeks < 12 else "OK"),
    "weekly_projections": weekly,
    "scenarios": scenarios,
    "assumptions": {
        "starting_cash": 0,
        "sales_cycle_weeks": "4-8 (from Target_Market.md triggers)",
        "avg_deal_size": "Jumpstart $1,497 / Install $3,497 / Retainer $4,997/mo",
        "close_rate": "Conservative - pipeline building from zero",
        "collection_terms": "50% upfront, 50% on delivery (standard for agency)",
        "operational_burn": "$200-300/week (tools, internet, minimal ops)"
    },
    "escalation_triggered": runway_weeks < 12,
    "escalation_target": "Finance Director" if runway_weeks < 12 else ("COO" if runway_weeks < 8 else None),
    "data_sources": {
        "company_state": "02_COO/company_state.md",
        "pricing": "04_Knowledge/Offers/Pricing_Packages.md",
        "target_market": "04_Knowledge/Company/Target_Market.md",
        "working_memory": "working_memory.finance (empty - first run)"
    }
}

# Write forecast artifact
forecast_path = f"/data/data/com.termux/files/home/CraftedWorkflowsOS/memory/working/finance/cash/cash_forecast_{current_week}_13wk.json"
with open(forecast_path, "w") as f:
    json.dump(forecast, f, indent=2)

print(f"Forecast written to: {forecast_path}")
print(f"Runway: {runway_weeks} weeks")
print(f"Status: {forecast['runway_status']}")
print(f"Escalation needed: {forecast['escalation_triggered']} -> {forecast['escalation_target']}")

# Cash position
cash_position = {
    "date": datetime.now().isoformat(),
    "cash": 0,
    "forecast_inflows": total_inflows,
    "forecast_outflows": total_outflows,
    "net": total_inflows - total_outflows,
    "runway_weeks": runway_weeks,
    "status": forecast["runway_status"]
}

print("\nCash Position:")
print(json.dumps(cash_position, indent=2))
