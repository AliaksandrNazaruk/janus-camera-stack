<!--
Thanks for your PR! Please fill in the sections below.
Drop sections that don't apply.
-->

## Summary

<!-- 1-3 bullet points: what this PR does and why. -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (API, config, or contract change)
- [ ] Docs only
- [ ] Internal refactor (no functional change)

## Layer scope (L0-L4)

- [ ] L0 (encoder / hardware adapter)
- [ ] L1 (mountpoint allocator)
- [ ] L2 (sensor lifecycle)
- [ ] L3 (Janus orchestration)
- [ ] L4 (FastAPI dashboard / API)
- [ ] Frontend
- [ ] Deployment / docs

## Verification

<!-- Tests run, manual verification steps. Be specific. -->

- [ ] `pytest tests/` — all green
- [ ] `pytest tests/test_architecture_fitness.py` — fitness green
- [ ] `ruff check app/` — clean
- [ ] Manual smoke test (if UI / streaming)
- [ ] Updated relevant docs

## Notes for reviewer

<!-- Anything subtle? Concerns? Areas you want extra eyes on? -->

## Breaking changes

<!-- If yes, describe migration path. Update CHANGELOG.md. -->

Closes #
