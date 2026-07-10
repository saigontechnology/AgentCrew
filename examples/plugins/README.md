# AgentCrew plugin verification examples

Run local-path verification:

```bash
uv run python examples/plugins/verify_local_plugin.py
```

Run real entry-point verification in an isolated uv environment:

```bash
uv run --with ./examples/plugins/entry_point_plugin python examples/plugins/verify_entry_point_plugin.py
```

Verify exact `AppEvents` payload-map coverage and representative payload contracts:

```bash
uv run python examples/plugins/verify_event_payload_map.py
```

Verify normalized Qt stream/image payload routing and partial startup rollback:

```bash
QT_QPA_PLATFORM=offscreen uv run python examples/plugins/verify_qt_event_payloads.py
uv run python examples/plugins/verify_plugin_initialization_rollback.py
```

The local verifier covers activation, configuration delivery, EventBus and hook registration, deterministic unload cleanup, activation rollback, deactivation-failure cleanup, disabled plugins, missing/failed dependencies, cycles, unrelated-plugin isolation, and duplicate-free reload. The entry-point verifier uses real `importlib.metadata` discovery in an isolated uv environment.

These scripts require no API key, network service, GUI, or deprecated test suite. They exit nonzero when verification fails. See [`PLUGIN_DEVELOPMENT.md`](../../PLUGIN_DEVELOPMENT.md) for the authoring contract.
