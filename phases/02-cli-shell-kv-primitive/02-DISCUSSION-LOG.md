# Phase 2: CLI Shell + KV Primitive - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 2-cli-shell-kv-primitive
**Areas discussed:** JSON envelope + schema_version, Key namespacing, KV exit codes + edge cases, Subcommand mounting structure

---

## JSON envelope + schema_version

### Q1: JSON shape for every `em-proj state` verb

| Option | Description | Selected |
|--------|-------------|----------|
| Common envelope | `{schema_version, status, data, error}` for every verb. Predictable for downstream parsers. | ✓ |
| Per-verb minimal shape | Each verb returns just what it needs (`get` → bare string). Lighter but per-verb shapes to parse. | |
| Hybrid: envelope on errors, raw on success | Success = bare value; errors = envelope. Distinguishes "empty string" from "missing key" only via the error path. | |

**User's choice:** Common envelope.
**Notes:** Phase 5 `/global-state` skill and Phase 6 gsd-sdk consumer both parse output across multiple verbs — common envelope means one parser handles everything forever.

### Q2: schema_version format

| Option | Description | Selected |
|--------|-------------|----------|
| Integer string ("1", "2", ...) | Bump only on breaking changes. Field additions don't bump. | ✓ |
| Semver ("1.0.0", ...) | More expressive but overkill for tiny CLI output schema. | |
| Date-stamped ("2026-05") | Visible age, less common, parsing surprises. | |

**User's choice:** Integer string.

### Q3: Error object shape (asked twice — first declined for clarification)

User initially asked "how easy is it to redefine these decisions later?" — I reframed the question after explaining that field additions are non-breaking but renames force a schema_version bump.

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal: {code, message} | Ship smallest viable surface; add details/retry_after later when a verb needs them. | ✓ |
| Pre-declared: {code, message, details, retry_after} (all optional) | Larger contract from day one; speculative fields. | |
| Documented-but-not-enforced extension | Ship minimal + docs note about future fields. | |

**User's choice:** Minimal.
**Notes:** Forward-extension is free; reversibility analysis demonstrated this and reframed the decision from "right forever" to "right now."

### Q4: JSON output formatting

| Option | Description | Selected |
|--------|-------------|----------|
| Compact, newline-terminated | Single-line `json.dumps(...)` + trailing newline. NDJSON-compatible. | ✓ |
| Pretty-printed (indent=2) | Easier to read by hand; breaks NDJSON; harder to grep. | |
| Compact by default, --pretty flag opt-in | Best of both, costs one flag per verb. | |

**User's choice:** Compact.

---

## Key namespacing in Redis

### Q1: Redis key prefix structure

| Option | Description | Selected |
|--------|-------------|----------|
| `state:kv:foo` | Two-segment prefix. Verb-family scoping for `state list` queries. | ✓ |
| `em-proj:state:kv:foo` | Three-segment prefix; defensive against multi-tenant Redis. | |
| `foo` (no prefix) | Simplest; collides with other Redis users on db=0. | |

**User's choice:** Two-segment `state:kv:foo`.

### Q2: `state list` output — strip prefix or show raw?

| Option | Description | Selected |
|--------|-------------|----------|
| Strip prefix — user sees what they typed | Symmetric with set/get/del. Prefix is implementation detail. | ✓ |
| Show raw `state:kv:foo` keys | Reveals namespace; conflicts with the get/set/del input convention. | |
| Strip by default, --raw flag | Best of both, costs one flag on `list`. | |

**User's choice:** Strip prefix.

### Q3: `state list` scope

| Option | Description | Selected |
|--------|-------------|----------|
| kv only | Returns only `SCAN MATCH state:kv:*`. Locks/claims get dedicated verbs. | ✓ |
| All `state:*` keys, kind-tagged | Single overview verb. Conflicts with future `state locks` / `state claims`. | |
| kv only + --include locks,claims flag | Default kv, opt into family. Adds surface for marginal value. | |

**User's choice:** kv only.

### Q4: Key character validation

| Option | Description | Selected |
|--------|-------------|----------|
| Allow `[a-zA-Z0-9_.-/]` only | Permissive but predictable. Rejects whitespace, colons, shell metacharacters. | ✓ |
| Allow anything Redis accepts | Maximum flexibility, minimum safety. | |
| Allow `[a-zA-Z0-9_.-/]` + colon | Permits user-namespaced keys but visually collides with our prefix. | |

**User's choice:** Strict regex.

---

## KV exit codes + edge cases

### Q1: `state get <missing>`

| Option | Description | Selected |
|--------|-------------|----------|
| Exit 2 + error envelope | Distinguishes missing-key from empty-value. Aligns with PROJECT.md exit-code spec. | ✓ |
| Exit 0, empty stdout | Treat missing as "empty"; conflates failure with empty. | |
| Exit 2, but stdout-empty on TTY | Hybrid: TTY silent, --json shows envelope. Inconsistent. | |

**User's choice:** Exit 2 + envelope.

### Q2: `state del <missing>`

| Option | Description | Selected |
|--------|-------------|----------|
| Idempotent: exit 0, no error | `rm -f` semantics. Scripts can `del foo` without checking existence. | ✓ |
| Strict: exit 2 + error envelope | Symmetric with `get missing`. Breaks `rm -f` ergonomics. | |
| Idempotent by default, --strict flag | Default friendly, opt-in strict. Extra flag. | |

**User's choice:** Idempotent.

### Q3: `state set foo new` on existing key without `--ttl`

| Option | Description | Selected |
|--------|-------------|----------|
| KEEPTTL: preserve existing TTL | Redis SET ... KEEPTTL. Matches user mental model. | ✓ |
| Reset TTL to no-expiry | Redis default. Surprising — updating value silently makes key persistent. | |
| Error if existing key has TTL | Forces clarity but punishes the common case. | |

**User's choice:** KEEPTTL.

### Q4: `state list` with 0 kv keys

| Option | Description | Selected |
|--------|-------------|----------|
| Exit 0, empty body | Empty is valid; scripts handle 0-line output naturally. | ✓ |
| Exit 2, not_found error | Conflates empty with failure. Painful for scripts. | |
| Exit 0, TTY prints '(no keys)' / JSON empty array | Human hint + clean JSON. Hints can be wrong. | |

**User's choice:** Exit 0 + empty body.

---

## Subcommand mounting structure

### Q1: Mount style for `em-proj state`

| Option | Description | Selected |
|--------|-------------|----------|
| Nested typer app | `state_app = Typer()` mounted via `add_typer`. Idiomatic; scales to session/message. | ✓ |
| Flat verbs `em-proj state-get` etc. | Hyphenated names; breaks the documented CLI shape. | |
| Single module, manual dispatch | Loses typer's auto-help and type checking. | |

**User's choice:** Nested typer app.

### Q2: JSON envelope helper location

| Option | Description | Selected |
|--------|-------------|----------|
| `em_proj/output.py` shared module | One module owns TTY detection + envelope + helpers. Single source of truth. | ✓ |
| Per-verb: each command builds its own JSON | Repetitive; schema drift risk. | |
| Typer callback/middleware | Fights the framework. | |

**User's choice:** Shared module.

### Q3: `--json` flag placement

| Option | Description | Selected |
|--------|-------------|----------|
| Per-verb flag, defaults to TTY-detect | Default None → `sys.stdout.isatty()`. Visible in every verb's --help. | ✓ |
| Root-level flag | Set once at root via Context. Less discoverable in --help. | |
| Both: root + per-verb | Overkill for one-shot invocations. | |

**User's choice:** Per-verb with TTY-detect default.

### Q4: File layout for the `state` subcommand family

| Option | Description | Selected |
|--------|-------------|----------|
| Package: `em_proj/state/` with verb + ops modules | `__init__.py` wires verbs; `kv.py` holds pure ops. Scales to Phase 3+ lock/claim. | ✓ |
| Single file: `em_proj/state.py` | Painful in Phase 3+ when lock/claim grow file to 800+ lines. | |
| Verb-per-file `em_proj/state/get.py` etc. | Over-granular for tiny functions. | |

**User's choice:** Package.

---

## Claude's Discretion

- Exact internal naming of helpers in `output.py` (`emit_ok` vs `print_success` vs `write_response`)
- `SCAN` vs `KEYS` for `kv_list` implementation (Phase 2 keyspace is small enough either works)
- Max value size cap — Redis caps at 512MB; em-proj should likely reject >1MB. Researcher to confirm threshold.
- List ordering — sorted-alphabetical preferred for predictability unless researcher finds a reason against
- typer auto-help formatting per verb — accept typer's defaults unless they look ugly

## Deferred Ideas

- `--pretty` flag on JSON output
- `em-proj:` umbrella prefix on Redis keys
- `--raw` flag on `state list` to show un-stripped keys
- `--include locks,claims` flag on `state list`
- `--strict` flag on `state del`
- Pre-declared error fields (`details`, `retry_after`) — add when Phase 3+ surfaces a need
- Documented-but-not-enforced extension convention for the error envelope
- Root-level `--json` flag
- Single-file `em_proj/state.py`
- Verb-per-file under `em_proj/state/`
- `em-proj health` subcommand (still deferred from Phase 1)

**Reframing moment:** When the error-shape question came up, the user asked "how easy is it to redefine these decisions later?" — leading me to explain that field additions are free (zero schema bump) but renames are expensive. This unlocked picking "minimal" without anxiety. Worth remembering: when asking about format/shape decisions, lead with reversibility cost so the user knows whether they're picking "right now" or "right forever."
