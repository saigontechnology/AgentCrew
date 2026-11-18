# Accessed resource provenance

AgentCrew records **successful resource accesses**, not every URL mentioned in a
conversation or every MCP resource advertised in system context. Memory XML is
canonical; SQLite schema 2 projects its resources and explicit entity links into
agent-scoped derived indexes. Chroma still stores the same portable XML.

## Capture and lifecycle

- Captured surfaces: `read_file` / ACP file reads, `fetch_webpage`, dynamically
  registered server-scoped MCP `get_resource`, processed chat and A2A attachments,
  and ACP embedded resources. An ACP resource link alone is not a fetch.
- One synchronous `tool.execute` after-hook uses a resolved-name extractor registry
  and resolved input. It does not copy or recursively scan arguments/results.
  MCP results retain their list interface and carry an explicit fetch outcome.
- A ContextVar dictionary is created/reset with the existing inference scope for
  chat, A2A, ACP, and jobs. Nested inference calls share their owning turn; distinct
  request tasks have independent dictionaries. Reset is in `finally`, including
  chat setup errors. There is no collector thread, queue, polling or access-time
  persistence.
- At most **32 distinct URIs per turn**, **4096 characters per URI**, and 256
  characters per captured display name. Invalid/control-character URIs and excess
  resources are ignored. These are URI metadata limits, not content-size limits.
- Successful pre-inference chat attachments use a bounded pending list, consumed
  at inference start and cleared on new/load conversation. A2A processing and ACP
  embedded resources already run inside their inference scope.
- `store_conversation` snapshots a tuple of frozen, slotted resource values. The
  existing memory queue/worker receives this immutable operation data. No result
  bodies, file bytes, stat calls, file hashing, re-fetches, or additional extraction
  model requests are introduced by the collector.

## URI identity and attachments

Custom/MCP URI spelling is preserved: scheme/host case, Unicode, percent escapes,
query strings and fragments are not rewritten. The deterministic resource ID is
SHA-256 of the exact canonical URI, not a hash of the resource's contents.
Local paths become lexical absolute `file://` URIs, with URI escaping. Capture does
not resolve symlinks or perform filesystem I/O; a symlink spelling remains the
spelling the agent accessed. This is intentional, not a guarantee of physical-file
identity.

HTTP attachments retain their original URL, never their processor temporary path.
Raw uploads use conversation-scoped `attachment://` locators, using existing
message/item IDs when available and a unique ephemeral ID otherwise. These identify
conversation evidence; **they do not retain bytes or promise a downloadable URL**.
Deleted processor paths are never resource locators. URI query strings may contain
sensitive values: resource provenance has the same retention and access controls as
its owning memory. Exact-URI functionality is not credential redaction.

## Canonical XML and relationships

```xml
<MEMORY>
  <ENTITIES>
    <ENTITY><NAME>Example Candidate</NAME></ENTITY>
  </ENTITIES>
  <RESOURCES>
    <RESOURCE uri="file:///home/user/cvs/example-candidate.pdf" name="Candidate CV">
      <ENTITY_REF relation="source_document">Example Candidate</ENTITY_REF>
    </RESOURCE>
    <RESOURCE uri="ui://mobbin/search-screens.html"/>
  </RESOURCES>
</MEMORY>
```

Legacy scalar `<RESOURCE>https://example.org/runbook</RESOURCE>` remains accepted.
Recognizable legacy paths are projected too. When historical CWD is unknown,
relative legacy paths remain relative (scheme empty); migration does not invent
an absolute historical location. Malformed/unrecognizable values produce parser
warnings; the original XML is preserved.

The existing extraction model may propose display names and links. A deterministic
postprocessor retains every captured URI, rejects invented/rewritten URIs and
requires a unique extracted entity name for links. Unlinked resources stay
unlinked. Consolidation unions original resources/explicit links, deduplicates
URIs and refuses model-created links to uncaptured historical resources. Linked
entity names omitted by a merge are retained. No relationship is inferred just
because an entity and a resource share a memory. Model-proposed links are extraction
claims, not independent verification of the resource's contents or identity.
Fallback/no-model storage still preserves URIs.

## SQLite migration and retrieval

Writable schema-1 databases upgrade transactionally to schema 2 on repository open.
The upgrade rebuilds derived units/resources and updates only derived parser fields;
canonical document, metadata JSON bytes/checksums, timestamps and migration ledger
remain intact. Failure rolls the upgrade back, including DDL/version changes.
Back up operational databases before upgrading. The first writable open does index
backfill work proportional to the stored corpus and can hold the writer lock; it
is not a constant-time startup operation. Older binaries cannot open schema 2.
Read-only schema-1 opens do not upgrade and fail schema validation; open a backed-up
copy with the current writable repository first.

`memory_resource` stores per-parent URI projections and retrieval-unit ownership.
`memory_resource_entity` stores explicit relation ownership. Composite foreign keys
prevent cross-memory links, and deletion cascades through both projections. Writes
use `executemany` inside the existing write transaction, with no extra commit.
Chroma-to-SQLite mapping derives the same projections from unchanged source XML;
migration counts and row verification include resources and relations.

Search does direct indexed URI lookup only for URI/path-like queries. Agent, active
status, session, date and explicit ID eligibility are applied before candidate caps.
Exact URI matches have the `exact_resource_uri` reason; explicit links are reported
separately as `explicit_entity_resource_link`. Ordinary ranking does not join the
resource tables. Selected hits hydrate resources/linked entity names in one batch,
in the same read snapshot. `search_memory(detail="evidence"|"full")` shows the exact
URI, display name and explicit linked entity/relation; full output retains canonical
XML. An unlinked legacy resource is searchable but is not attributed to an entity.

## Verification

Use the project's uv environment (dependencies already installed):

```sh
uv run python -m pytest verification/memory/test_resource_provenance.py -q
uv run python verification/memory/benchmark_resource_provenance.py \
  --records 2000 --repeats 200 --compare-root /path/to/pre-feature-checkout
uvx ruff check <changed-python-files> --statistics
uvx ruff format --check <changed-python-files>
```

The benchmark emits raw samples as JSON and uses alternating, same-process paired
searches when `--compare-root` is supplied. It never changes production logging or
polling. The deprecated `tests/` folder is not used.

Design/API references: [Python ContextVar lifecycle and async tasks](https://docs.python.org/3.12/library/contextvars.html),
[dataclass frozen/slots](https://docs.python.org/3.12/library/dataclasses.html),
[SQLite foreign keys and cascades](https://sqlite.org/foreignkeys.html),
[SQLite transactions](https://sqlite.org/lang_transaction.html), and
[Python lexical absolute paths](https://docs.python.org/3.12/library/os.path.html#os.path.abspath).
