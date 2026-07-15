"""Levelling Up Fund delivery-status pipeline (North West England MVP).

Modules:
    scrape_base  -- Step 1: build the base project dataset from gov.uk.
    retrieve     -- Step 3: Tavily search-client factory used by the agent.
    agent        -- Step 3: LangGraph ReAct classifier that searches in a loop.
    run          -- Step 3: classify every project into predictions.csv.
    validate     -- Step 4: score predictions vs. ground truth -> validation_report.md.
"""
