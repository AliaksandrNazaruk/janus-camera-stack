"""Application layer — use-cases that orchestrate infra adapters (app/services/*).

Plain functions, no web framework: validate inputs, sequence side effects through
adapters, audit, and shape the result. Routes (app/routes/*) call into here and map
the result to HTTP; adapters do the actual side effect (subprocess / file IO / HTTP).
See docs/design/ADMIN_DASHBOARD_SPLIT.md.
"""
