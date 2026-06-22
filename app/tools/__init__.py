"""Operator CLI tools (boot reconciler, maintenance scripts).

Modules in this package run as `python3 -m app.tools.<name>` from systemd
units. They import the same app.services as the L4 HTTP service — single
source of truth for lifecycle decisions, no HTTP roundtrip needed.
"""
