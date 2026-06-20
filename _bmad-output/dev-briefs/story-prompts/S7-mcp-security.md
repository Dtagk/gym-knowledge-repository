# Sprint Fix S7: mcp-security

**Branch:** `fix/mcp-security`  
**Files:** `yt_kg/mcp_server.py` only

## Issue to fix

### Cypher read-only guard allows `WITH` as a query prefix, adding unnecessary attack surface

`yt_kg/mcp_server.py`, lines 16–17.

```python
_READ_PREFIX = re.compile(r"^\s*(MATCH|RETURN|UNWIND|WITH|OPTIONAL\s+MATCH)\b", re.IGNORECASE)
_WRITE_TOKENS = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|COPY|INSTALL|LOAD|CALL)\b", re.IGNORECASE)
```

Two problems:

1. `WITH` is allowed as a query-starting keyword. In Kuzu's Cypher, `WITH` is a pipe clause
   between query parts, not a valid standalone query start. Allowing it means `WITH 1 AS x CREATE ...`
   passes the prefix check (caught by `_WRITE_TOKENS`, but adds unnecessary surface area).
   Valid read queries always start with `MATCH`, `RETURN`, `UNWIND`, or `OPTIONAL MATCH`.

2. `CALL` is in the write-token denylist, which will incorrectly block legitimate read queries
   that use `CALL` subqueries or `CALL db.labels()` in Kuzu. `CALL` is not a write operation.
   The actual write verbs in Kuzu are: `CREATE`, `MERGE`, `DELETE`, `DETACH DELETE`, `SET`,
   `REMOVE`, `DROP TABLE`, `DROP SEQUENCE`, `COPY FROM`, `INSTALL`, `LOAD EXTENSION`.

**Fix:**

```python
_READ_PREFIX = re.compile(r"^\s*(MATCH|RETURN|UNWIND|OPTIONAL\s+MATCH)\b", re.IGNORECASE)
_WRITE_TOKENS = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|COPY|INSTALL|LOAD)\b", re.IGNORECASE)
```

Changes:
- Remove `WITH` from `_READ_PREFIX`
- Remove `CALL` from `_WRITE_TOKENS`

## Current file content (relevant section)

```python
_READ_PREFIX = re.compile(r"^\s*(MATCH|RETURN|UNWIND|WITH|OPTIONAL\s+MATCH)\b", re.IGNORECASE)
_WRITE_TOKENS = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|COPY|INSTALL|LOAD|CALL)\b", re.IGNORECASE)
```

Read `yt_kg/mcp_server.py` in full before making changes.

## Acceptance criteria

- `WITH` is removed from `_READ_PREFIX`
- `CALL` is removed from `_WRITE_TOKENS`
- No other changes to the file
