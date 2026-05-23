"""Deterministic lead scoring + ranking.

The agent shape is a deterministic feature pipeline (this package) followed
by an LLM rationale layer (rationale.py — stubbed; filled in by a separate
session). The feature math here is pure and unit-testable; the LLM never
participates in ranking.
"""
