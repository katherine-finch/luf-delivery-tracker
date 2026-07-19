"""Levelling Up Fund delivery-status pipeline (North West England MVP).

Modules:
    scrape_base  -- build the base project dataset from gov.uk.
    retrieve     -- Tavily search-client factory used by the agent.
    agent        -- LangGraph ReAct classifier that searches in a loop.
    run          -- classify every project into predictions.csv.
"""
