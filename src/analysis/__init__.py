"""Wiring between storage, baselines and the scorer.

This layer exists because `src.scoring` is forbidden from importing `src.storage` and
`src.baselines` — it takes forecasts as data and nothing else. Something has to assemble
that data, and it is deliberately not the scorer.
"""
