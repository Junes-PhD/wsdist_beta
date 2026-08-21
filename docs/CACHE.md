# Simulation Cache Implementation Plan

## Current findings

- Persistent simulation results are stored in the Qt cache directory as `simulation-results.sqlite3`.
- Overnight warming writes `quick-look` rows through `_run_overnight_cache_task()`.
- Normal Quick Look currently checks only the in-process `_quick_lookup_cache`; it does not read persistent `quick-look` rows after restart.
- Normal Quick Look also does not persist newly calculated results to `SimulationCache`.
- `SimulationCache.get()` and `put()` open, commit, and close a SQLite connection for each operation. This is acceptable for occasional lookups but not for an optimizer inner loop.
- The optimizer already has useful exact per-run gearset memoization, dominated-candidate pruning, and incremental PDT/MDT/DT totals. New work must extend those paths rather than duplicate them.
- `create_player.add_gear_stats()` contains job-specific and set-specific equipment behavior. Raw gear contributions can be shared broadly, but finalized player/action results must retain relevant job and named-equipment behavior flags.

## Required behavior

1. A warmed Quick Look result must be usable after restarting the application.
2. A normal Quick Look result should be persisted for later reuse.
3. Persistent cache reads and writes must not become a per-candidate SQLite bottleneck.
4. Equivalent gear, player, enemy, and action profiles should share intermediate work without changing exact results.
5. Cache invalidation must be dependency-specific where practical. A WS formula change should not invalidate item contribution data.
6. Cache hits must be observable by layer and measurable against uncached execution.

## Staged implementation

### Phase 1 - Persistent Quick Look integration

- **Started:** the first implementation slice is complete and covered by the regression suite.
- [x] Add a bulk `load_recent()` API to `SimulationCache`.
- [x] Hydrate the in-process Quick Look LRU at application startup.
- [x] Build the Quick Look request from UI state before constructing a player.
- [x] On an in-memory miss, check the persistent `quick-look` row.
- [x] On a calculation, store the exact `{text, output}` payload persistently when caching is enabled.
- [x] Add a recent-row hydration regression test.
- [ ] Replace synchronous writes with a batched/background writer after profiling UI impact.

Phase 1 implementation files:

- `simulation_cache.py`: `SimulationCache.load_recent()`.
- `qt_gui_main.py`: normalized Quick Look keys, startup hydration, persistent fallback, and persistent writes.
- `test_simulation_cache.py`: startup hydration coverage.

Verification status: 93 tests pass in the focused cache/Qt/optimizer suite.

### Phase 2 - Immutable reusable profiles

Create `combat_profiles.py` with immutable/serializable records for:

- item contributions keyed by item/augment identity;
- job/subjob/master-level packages;
- buff and ability packages;
- job-independent raw gear material;
- finalized player snapshots;
- enemy/debuff snapshots.

Use raw gear material as the first cross-job sharing boundary. Do not cache mutable `create_player` instances.

### Phase 3 - Exact action contexts and LUTs

Add action-specific contexts for melee, ranged, magic, and enemy calculations. Include every relevant numeric field and behavior flag read by the action code. Use exact sparse memoization first, then dense LUTs for bounded pure functions such as hit rate, fSTR, and static WS coefficients.

Do not build a full job-by-gear-by-enemy-by-WS Cartesian LUT. Use shared intermediate profiles and deduplicate equivalent action signatures.

### Phase 4 - Optimizer reuse

- [x] Freeze each candidate item once per worker and reuse its gear key.
- [x] Pass that same key into the player cache instead of rebuilding it.
- [x] Keep optimizer-only player-cache hits read-only, avoiding deep copies.
- [x] Store compact `(metric, output)` evaluation records except for substat searches.
- [x] Increase the compact worker LRU and report hits, misses, and evictions.
- [x] Cache average-WS formulas by effective stats/abilities plus named-gear exceptions.
- [x] Rank equipment candidates by normalized value for earlier strong results;
  ranking changes visit order only and removes no candidates.
- [x] Cache item PDT/MDT/DT contributions and pre-filter items that cannot
  participate in any defensively valid set using an optimistic bound.
- Extend the existing one/two-slot delta approach from defense totals to raw gear aggregates.
- Recalculate non-additive set and conditional effects safely.
- Keep hot profile/action caches in worker RAM.
- Return completed cache rows to the parent and persist them in batches; never query SQLite per candidate.

Runtime cache sizes can be tuned before launching the application:

```powershell
$env:FFXI_PLAYER_CACHE_SIZE='1024'
$env:FFXI_EVAL_CACHE_SIZE='16384'
$env:FFXI_WS_FORMULA_CACHE_SIZE='16384'
python qt_gui_main.py
```

Substat searches default to a smaller 4,096-entry evaluation LRU because their
records must retain a `Player` for constraint checks. Ordinary searches retain
only compact numeric results and default to 16,384 entries.

### Phase 5 - Layered warming and persistence

Warm a dependency graph:

```text
items -> gear material -> player profiles -> action contexts -> final results
```

Use separate version tags for item data, player construction, physical formulas, magic formulas, and WS metadata. Keep large static numeric LUTs in memory-mappable files and use SQLite for persistent profiles/results.

## Verification

Preserve existing golden, parity, optimizer, and cache-corruption tests. Add tests proving:

- warmed Quick Look rows are reused after a fresh cache instance;
- relevant input changes miss while irrelevant action inputs reuse safely;
- cached profiles match `create_player` exactly;
- cached action contexts match uncached outputs;
- serial and parallel optimizer results remain identical;
- each cache layer reports hit/miss and timing data.

Baseline command:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest test_simulation_cache.py test_qt_gui_helpers.py test_performance_parity.py
```
