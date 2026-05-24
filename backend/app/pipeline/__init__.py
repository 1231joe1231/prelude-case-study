"""Pipeline orchestration: input switching + manual run + stage tracking.

Default startup ingests CSVs into SQLite tables only. The full ranking
pipeline (persona inference → feature extraction → scoring → LLM rationale)
is triggered explicitly via POST /api/pipeline/run from the frontend's
Pipeline page so the user controls when (and on which dataset) the heavy
LLM work happens.
"""
