# Test module convention (revised)

## The rule as written

> Test modules should be runnable with `pytest` but not done in the `pytest`
> style or using `unittest`. Tests should run on their own, display useful
> information about the tests, **return true/false or 1/0**, and accumulate
> success in `main()` to show the final score.

## Why it drifted

"Return true/false or 1/0" does not say which value means success, and the two
readings collide at the `exit()` boundary:

- **Python truthiness**: `1` is `True`, so `return 1` reads as "passed"
- **POSIX exit status**: `0` is success, so `exit(1)` means "failed"

Both readings are individually reasonable. Applying the first to `main()` and
then writing `exit(main())` produces a module that reports success as a shell
failure. Four modules ended up that way; four others using the identical
`exit(main())` idiom returned `0` on success and were correct. Same idiom,
opposite meaning, which is drift rather than convention.

## The rule, restated

**Test functions** return `True` on pass, `False` on fail. Unchanged — this
part was never ambiguous and reads naturally at the call site.

**`main()`** returns a truthy value when everything passed. Whether that is
`True`, or a count, or `passed == total`, does not matter.

**The `exit()` call always inverts**, and is always written the same way:

```python
if __name__ == '__main__':
    exit(0 if main() else 1)
```

This is the only line where the POSIX meaning applies, and writing the
inversion explicitly at that one point keeps the two number systems from
being confused anywhere else.

Never write `exit(main())`. It silently couples the two meanings, and it is
correct or backwards depending on a decision made hundreds of lines earlier.

## Why this is worth enforcing

The inversion is not cosmetic. It made exit status **anti-correlated** with
reality across all four affected modules:

| Module | Actually | Old exit | Read as |
|---|---|---|---|
| `test_generate_column_config_fileops` | 1/7 failing | 0 | pass |
| `test_generate_column_config_processor` | 1/5 failing | 0 | pass |
| `test_seed_donor_formulas_basic` | passing | 1 | fail |
| `test_seed_donor_formulas_functional` | passing | 1 | fail |

The two genuinely broken modules looked green, which is how three real bugs
survived in `generate_column_config` — a pandas 3.0 incompatibility, a
construction-time validation that broke capability discovery and variable
substitution, and a missing `get_operation_type()` override.

# End of file #
