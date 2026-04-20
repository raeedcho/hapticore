# ADR-006: Monorepo for Python, C++, and firmware

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** The system spans three languages (Python, C++17, Arduino/C++) and multiple deployment targets (Linux workstation, Teensy microcontroller). Should these live in one repository or several?

## Decision

Single monorepo containing the Python package, C++ haptic server, Teensy firmware, configuration templates, documentation, and CI scripts.

## Rationale

The components are tightly coupled through shared message schemas and coordinated releases. When the HapticState message format changes, the C++ publisher, Python subscriber, and tests must all update atomically. A monorepo ensures this happens in a single commit rather than a coordinated multi-repo release dance. Shared CI can verify Python–C++ interoperability in one pipeline. AI coding agents work significantly better with monorepos because all relevant context (schema definitions, both sides of the interface, tests) is accessible in one codebase search.

## Structure

```
hapticore/
├── python/hapticore/     # pip-installable Python package
├── cpp/haptic_server/    # CMake C++ project
├── firmware/teensy/      # Arduino/PlatformIO project
├── configs/              # YAML experiment configs
├── tests/                # Python tests (C++ tests live in cpp/)
├── docs/                 # architecture, guides, ADRs
├── .github/              # CI workflows, copilot-instructions.md
└── pyproject.toml        # Python package metadata
```

## Consequences

- Repo size may grow with C++ build artifacts if `.gitignore` is not maintained carefully. Build directories (`build/`, `__pycache__/`, `.eggs/`) must be excluded.
- Contributors working only on Python tasks don't need the C++ toolchain unless they run hardware tests. The `MOCK_HARDWARE=ON` CMake flag and Python mock interfaces handle this.
- Git history for Python and C++ is interleaved, making per-language history harder to browse. Mitigated by consistent directory-scoped commits.
