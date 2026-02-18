# fast-fstests

Parallelizes Linux fstests across multiple mkosi QEMU VMs. Achieves 3-7x speedup over sequential execution.

Two packages live in `src/`:
- **`fastfstests`** — CLI entry point and fstests/mkosi-specific logic
- **`parallelrunner`** — reusable parallel test runner framework (generic, knows nothing about fstests or mkosi)

## Commands

```sh
uv run ff              # run tests
uv run ff --help       # show all CLI options
uv run ff --list       # list matched tests without running
uvx basedpyright src/  # type check
uv run pytest          # run unit tests
```

Configuration is loaded from `config.toml` in the project root (override with `FAST_FSTESTS_CONFIG_PATH` env var). CLI arguments override config file values.

## Architecture

```
src/
├── fastfstests/              # CLI + fstests-specific logic
│   ├── __main__.py           # Entry point: config loading → test collection → runner
│   ├── config.py             # Dataclass config with tyro CLI + mashumaro TOML
│   ├── fstests.py            # FSTest class, test collection/filtering/expansion
│   └── supervisors/
│       └── mkosi.py          # MkosiSupervisor: VM lifecycle, SSH test execution
└── parallelrunner/           # Generic parallel test runner framework
    ├── supervisor.py         # Abstract Supervisor base class (async context manager)
    ├── test.py               # Abstract Test + TestResult + TestStatus enum
    ├── test_runner.py        # TestRunner: asyncio workers pulling from shared queue
    └── output/
        ├── __init__.py       # Output: rich TUI, result file logging, regression detection
        └── rich_plotext.py   # Plotext histogram integration
```

**Key separation**: `parallelrunner` defines abstract `Test` and `Supervisor` interfaces. `fastfstests` provides the concrete implementations (`FSTest`, `MkosiSupervisor`). Do not add fstests or mkosi-specific logic to `parallelrunner`.

## Data Flow

1. **Config loading** — `config.toml` parsed via mashumaro, CLI args merged via tyro
2. **Test collection** — expand groups/globs, apply filesystem and exclusion filters, optional randomization
3. **VM spawn** — create N `MkosiSupervisor` instances, each launching a QEMU VM via mkosi
4. **Parallel execution** — asyncio workers (one per supervisor) pull tests from a shared queue
5. **Result collection** — stdout/stderr captured, artifacts collected from VM, results logged to disk
6. **Reporting** — summary with pass/fail/skip/error counts, optional failure list, slowest tests, histogram, regressions

## Configuration System

- `config.toml` loaded first, CLI args override via `tyro.cli(Config, default=...)`
- Key dataclasses in `config.py`: `Config`, `TestSelectionOptions`, `MkosiOptions`, `CustomVMOptions`, `OutputOptions`, `TestRunnerOptions`
- Custom types: `CommaSeparatedList`, `SpaceSeparatedList` (annotated strings with custom parsers)
- `tyro.conf.OmitArgPrefixes` flattens the CLI namespace so nested fields like `mkosi.num` become `--mkosi.num`
- Config changes require updating both the dataclass field and any relevant TOML/CLI handling

## Key Patterns

- Async context managers for supervisor lifecycle (`__aenter__`/`__aexit__`)
- `asyncio.TaskGroup` for parallel worker execution in `TestRunner`
- Same dataclass drives both TOML deserialization (mashumaro) and CLI parsing (tyro)
- Test results stored in `results/tests/{name}/{timestamp}/` with `results/latest/` symlinks
- Type hints throughout; `py.typed` marker present in `parallelrunner`
- New supervisor types should subclass `parallelrunner.supervisor.Supervisor`
- New test types should subclass `parallelrunner.test.Test`

## Dependencies

- **Core**: `mashumaro[toml]`, `rich`, `tyro`
- **Optional**: `plotext` (histogram output via `--print-duration-hist`)
- **System**: `mkosi`, `mkosi-kernel`, `fstests`
- **Build**: `hatchling`
- **Dev**: `pytest`, `basedpyright`

## Code Quality

- Run `uvx basedpyright src/` after making changes. Changes must not introduce new linter errors.
- If a warning is unavoidable (e.g., `Any` leaking from external libraries), leave it as-is. Do NOT add `# pyright: ignore` or `# type: ignore` suppression comments — they pollute the code.

## Testing

- Run `uv run pytest` after making changes to verify nothing is broken.
- Tests live in `tests/`, mirroring the `src/` package structure: `tests/parallelrunner/` and `tests/fastfstests/`.
- When adding new logic or modifying existing behavior, add or update corresponding tests.
- `parallelrunner` tests use a `MockSupervisor` to test orchestration without real VMs. Reuse it for new TestRunner tests.
- `fastfstests` tests use `tmp_path` fixtures for filesystem operations and `unittest.mock.patch` to mock `mkgroupfile()` (which calls a subprocess).
- MkosiSupervisor, Output (Rich TUI), and `__main__.py` are not unit tested — they depend on VMs, terminal rendering, and CLI integration respectively.

## Git Workflow

- **Every commit must be atomic** — one logical change per commit.
- **Respect package boundaries** — changes to `parallelrunner` and `fastfstests` go in separate commits. Complete and commit one package's changes before starting the next.
- When working on multiple changes, complete each one sequentially: make edits, verify/test, commit, then move to the next change.
- Never batch unrelated changes into a single commit. Splitting apart files with mixed changes after the fact is error-prone — keep things separate from the start.

## Important Notes

- Do not run `mkosi` or QEMU commands during exploration — they spin up VMs.
- The entry point is registered as both `fast-fstests` and `ff` (see `pyproject.toml` scripts).
- `config.toml.example` and `fast-fstests.conf.example` show reference configurations.
- **All user-facing changes (new CLI flags, config options, behavior changes) must include corresponding updates to `README.md`.**
