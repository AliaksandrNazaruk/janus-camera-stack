# Documentation Index

All project documentation lives here. The repository root keeps only the
conventional files GitHub recognizes (`README`, `LICENSE`, `NOTICE`,
`CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`, `CHANGELOG`) plus the two
governance docs (`SOURCE_OF_TRUTH.md`, `PROJECT_FILE_MANIFEST.md`).

## Current state (read first)
| Document | Scope |
|---|---|
| [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md) | **Authoritative current-state anchor** — done vs. debt after route-purity Phase 7 |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | Ranked architectural debt + next-wave plan (9 → 10 → 11) |
| [LEGACY_COMPATIBILITY.md](LEGACY_COMPATIBILITY.md) | What looks legacy but is load-bearing (don't delete) |
| ../SOURCE_OF_TRUTH.md | Canonical sources, generated outputs, the `realsense_mux` roles |
| ../PROJECT_FILE_MANIFEST.md | All 651 files classified + archive profiles |

## Getting Started & Deployment
| Document | Scope | Audience |
|---|---|---|
| [INSTALL.md](INSTALL.md) | Automated single-host install (`install.sh`) + options | Operators |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Manual clean-room install on a new Pi5 from bare OS | DevOps |
| [DEPLOYMENT_CLOUD.md](DEPLOYMENT_CLOUD.md) | Docker / Kubernetes / Helm (cloud control plane) | DevOps |
| [TUTORIAL_USB_WEBCAM.md](TUTORIAL_USB_WEBCAM.md) | End-to-end USB webcam → browser stream (no RealSense) | First-time users |
| [DEPTH_CAMERA_DEPLOY.md](DEPTH_CAMERA_DEPLOY.md) | Depth-camera node (192.168.1.55) deployment & operations | Operators (depth node) |
| [DEPLOY_COLOR_FRAME.md](DEPLOY_COLOR_FRAME.md) | `/color_frame` endpoint on the depth node | Operators (depth node) |

## Reference
| Document | Scope | Audience |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layer model (L0–L4), data flow, configuration model | Engineers |
| [CONTRACT.md](CONTRACT.md) | L4 control-plane contract (boundaries, allowed imports, endpoints) | Engineers |
| [ADAPTERS.md](ADAPTERS.md) | Camera adapter taxonomy + "add your own adapter" SDK | Stack extenders |
| [DEPTH_SEMANTIC_CONTRACT.md](DEPTH_SEMANTIC_CONTRACT.md) | Depth data format & semantics (binding for both nodes) | Engineers |
| [SLO.md](SLO.md) | Service level objectives (ICE connect, TTFF, MTTR, loss) | Engineers / on-call |

## Operations
| Document | Scope | Audience |
|---|---|---|
| [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md) | "If X happens, do Y" — production response procedures | On-call operators |
| RELEASE_GATE.md | Release gate criteria (boot, external, recovery, soak) | Release owners |
| [BACKLOG.md](BACKLOG.md) | Live backlog + known gaps | Maintainers |

## Testing
| Document | Scope | Audience |
|---|---|---|
| [TESTING.md](TESTING.md) | Test strategy, coverage model, markers + per-layer test matrix | Engineers |
| [RESILIENCE_TESTING.md](RESILIENCE_TESTING.md) | Fault-injection catalog (F01–F11), executable drills, 8h soak | Engineers / on-call |

## Research / Historical
| Document | Scope | Audience |
|---|---|---|
| STACK_NODE10_RESEARCH.md | As-built audit of the color node (.10): land-mines, ADRs, findings | Engineers (deep dive) |
