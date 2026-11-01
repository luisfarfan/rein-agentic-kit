# Evidence-backed impact

The current agent investigates the code; Python normalizes its report. This is
not static analysis and does not verify whether cited evidence is true. Keep
references reviewable and include negative evidence before marking a dimension
low or not applicable. A short change may use one reference for several dimensions.

Optional report format (all omitted dimensions become UNKNOWN):

```json
{
  "dimensions": {
    "scope": {"level": "low", "evidence": ["settings/currency.py:42 enables currencies; test_currency.py:18 covers EUR"]},
    "money": {"level": "high", "evidence": ["billing/convert.py:63 rounds settlement amounts"]}
  }
}
```

Levels: `low`, `high`, `unknown`, `not_applicable`. Dimensions: `scope`,
`implementation`, `operations`, `uncertainty`, `persistence`, `security`,
`concurrency`, `contracts`, `tests` (high = inadequate coverage), `side_effects`,
`money`. Missing evidence overrides a claimed low/high/not-applicable value to
UNKNOWN. Inspect uncertain boundaries before simplifying the strategy.

Reassess on a new dependency, changed contract, migration, permission scope,
verification failure or quota change. Reuse already established evidence; do not
repeat full reconnaissance simply because implementation started.
