"""Normalize evidence supplied by the investigating session. No text classification or I/O."""
from __future__ import annotations

DIMENSIONS = ('scope', 'implementation', 'operations', 'uncertainty', 'persistence',
              'security', 'concurrency', 'contracts', 'tests', 'side_effects', 'money')
LEVELS = ('low', 'high', 'unknown', 'not_applicable')


def assess(report: dict) -> dict:
    """Missing evidence is UNKNOWN, including a claimed low/not-applicable impact.

    Evidence references are declarations by the current agent, not proof that a
    file was read or a test passed. The caller must inspect the cited code/tests.
    """
    if not isinstance(report, dict):
        raise ValueError('impact report must be an object')
    dimensions = report.get('dimensions', {})
    if not isinstance(dimensions, dict):
        raise ValueError('dimensions must be an object')
    out = {}
    for name in DIMENSIONS:
        item = dimensions.get(name, {})
        if not isinstance(item, dict):
            raise ValueError('%s must be an object' % name)
        level = item.get('level', 'unknown')
        if level not in LEVELS:
            raise ValueError('%s: invalid level %r' % (name, level))
        evidence = item.get('evidence', [])
        if not isinstance(evidence, list) or any(not isinstance(e, str) or not e.strip() for e in evidence):
            raise ValueError('%s: evidence must contain nonempty references' % name)
        out[name] = {'level': level if evidence else 'unknown', 'evidence': evidence[:]}
    unknowns = [k for k, v in out.items() if v['level'] == 'unknown']
    risks = [k for k, v in out.items() if v['level'] == 'high']
    return {'schema_version': 1, 'dimensions': out, 'risks': risks, 'unknowns': unknowns,
            'discovery': 'deeper' if unknowns or out['uncertainty']['level'] == 'high' else 'sufficient',
            'confidence': 'UNKNOWN' if unknowns else 'evidence_supplied'}
