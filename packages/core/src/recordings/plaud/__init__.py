"""Plaud's payloads (spec §9.1, §9.1.1): the normaliser and reconcile, as pure functions.

Stage 2a uses them to import audio-router's snapshots; stage 2b's sync feeds them Plaud's own. One
module here makes network calls: `client`, the read-only Plaud client, which only the worker
imports (stage 2b spec §10.1). No other module does.
"""
