# Atlas Guides — Finalization Protocol

## Purpose
Control the single whole-build finalization pass.

## Preconditions

All phases must have passed their Phase Gates.

## Baseline

Before Finalizer modifications:

1. run the configured pre-finalization verification profile;
2. record repository state;
3. create a `FINALIZATION_BASELINE` checkpoint.

The baseline must be recoverable by the runtime.

## Finalizer

Finalizer may perform only safe, objectively supported cleanup.

Substantive behavioral/architectural/security findings are reported and do not get fixed by Finalizer.

## Post-Finalization

After Finalizer changes:

1. run the complete configured verification profile;
2. compare repository state;
3. verify final standards and scope;
4. execute finalization gate.

## Failure

If post-finalization verification fails:

```text
rollback to FINALIZATION_BASELINE
→ BLOCKED
```

If rollback cannot be performed safely, preserve the changes and mark the build BLOCKED for human intervention.

## Finalization Gate

PASS requires:

- all phases complete;
- final verification profile passes;
- no unresolved substantive findings;
- safe cleanup complete;
- documentation synchronized;
- scope/blast-radius checks pass.

Only then is the project `COMPLETE`.
