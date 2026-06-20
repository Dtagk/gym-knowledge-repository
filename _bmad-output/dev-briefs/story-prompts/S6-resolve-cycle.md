# Sprint Fix S6: resolve-cycle

**Branch:** `fix/resolve-cycle`  
**Files:** `yt_kg/resolve.py` only

## Issue to fix

### Union-Find path traversal has no cycle/depth guard

`yt_kg/resolve.py`, the entity deduplication loop (~lines 162–190).

The code builds a union-find structure in a plain dict (`merged_into`) then resolves roots via:

```python
root_i = i
while root_i in merged_into:
    root_i = merged_into[root_i]
```

This loop has no iteration limit. While the current merge logic prevents direct cycles (the
`if root_i != root_j` guard fires before writing), any future change or edge case that writes
a self-referential entry would cause an infinite loop and hang the pipeline.

**Fix:** Replace the bare `while` path traversal with a standard union-find helper that includes
path compression and a depth guard:

```python
def _find(merged_into: dict, x: int) -> int:
    depth = 0
    while x in merged_into:
        parent = merged_into[x]
        # path compression: point directly to grandparent
        if parent in merged_into:
            merged_into[x] = merged_into[parent]
        x = merged_into[x]
        depth += 1
        if depth > 10000:
            raise RuntimeError(f"Union-Find cycle detected at node {x}")
    return x
```

Replace **all four** path-traversal while loops in the function with calls to `_find(merged_into, i)`
and `_find(merged_into, j)`. There are two in the merge loop (finding root_i and root_j) and
two in the canonical_ids assignment loop.

## How to find the right location

Read `yt_kg/resolve.py` in full. The loops to replace look like:
```python
root_i = i
while root_i in merged_into:
    root_i = merged_into[root_i]
```

## Acceptance criteria

- A `_find(merged_into, x)` helper is added with path compression and a depth guard of 10000
- All four `while x in merged_into` traversal loops are replaced with `_find()` calls
- No other changes to the file
