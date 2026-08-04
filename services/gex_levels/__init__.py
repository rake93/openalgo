"""
GEX Levels — pure Black-76 math for dealer gamma exposure.

Everything in this package is IO-free: plain inputs to plain outputs, no
network, no database, no clock beyond what is passed in. Broker-touching
orchestration lives in `services/gex_levels_service.py`.
"""
