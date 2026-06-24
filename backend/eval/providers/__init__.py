"""Cheap place-data provider adapters for the recommendation-quality benchmark.

Each adapter turns a hosted Places API into a list of `PlaceCandidate`, the same
shape the production OSM/Google pipeline produces, so the benchmark can rank them
through the real scoring code and measure how far each one drifts from the
OSM+Google baseline. See `eval/PROVIDER_BENCHMARK.md`.
"""
