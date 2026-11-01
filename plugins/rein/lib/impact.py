"""Normalize investigated impact; v2 optionally checks cited files, never classifies request text."""
from __future__ import annotations

DIMENSIONS = ('scope', 'implementation', 'operations', 'uncertainty', 'persistence',
              'security', 'concurrency', 'contracts', 'tests', 'side_effects', 'money')
LEVELS = ('low', 'high', 'unknown', 'not_applicable')


def assess(report: dict, roots=None) -> dict:
    """Missing evidence is UNKNOWN, including a claimed low/not-applicable impact.

    Evidence references are declarations by the current agent, not proof that a
    file was read or a test passed. The caller must inspect the cited code/tests.
    """
    if not isinstance(report, dict):
        raise ValueError('impact report must be an object')
    version = report.get('schema_version', 1)
    if type(version) is int and version == 2:
        return _assess_v2(report, roots)
    if type(version) is not int or version != 1:
        raise ValueError('unsupported impact schema_version')
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


CLASSIFICATIONS = ('implementation', 'risk', 'uncertainty', 'repo_scope',
                   'planning', 'independent_verification')
FINDINGS = ('existing', 'missing', 'dependencies', 'risks')
RISK_FACETS = ('operations', 'persistence', 'security', 'concurrency', 'contracts',
              'tests', 'side_effects', 'money')


def _assess_v2(report, roots):
    import evidence
    checked = evidence.check(report.get('evidence', {}), roots)

    def references(item):
        ids = item.get('evidence', [])
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids):
            raise ValueError('evidence must be a list of reference IDs')
        return ids, bool(ids) and all(checked.get(i, {}).get('status') == 'current' for i in ids)

    def dimensions(raw, names):
        if not isinstance(raw, dict) or set(raw) - set(names):
            raise ValueError('unsupported classification or risk facet')
        out = {}
        for name in names:
            item = raw.get(name, {})
            if not isinstance(item, dict) or item.get('level', 'unknown') not in LEVELS:
                raise ValueError('%s: invalid classification' % name)
            if not isinstance(item.get('reason', ''), str):
                raise ValueError('%s: reason must be text' % name)
            ids, valid = references(item)
            out[name] = {'level': item.get('level', 'unknown') if valid else 'unknown',
                         'evidence': ids, 'reason': item.get('reason', '')}
        return out

    classification = dimensions(report.get('classification', {}), CLASSIFICATIONS)
    # Only declared facets are assessed; unknown safety conclusions stay visible
    # in the mandatory overall risk and independent-verification classifications.
    raw_facets = report.get('risk_facets', {})
    if not isinstance(raw_facets, dict) or set(raw_facets) - set(RISK_FACETS):
        raise ValueError('unsupported risk facet')
    facets = dimensions(raw_facets, tuple(raw_facets))
    raw_findings = report.get('findings', {})
    if not isinstance(raw_findings, dict) or set(raw_findings) - set(FINDINGS):
        raise ValueError('findings must separate existing, missing, dependencies and risks')
    findings = {}
    for category in FINDINGS:
        items = raw_findings.get(category, [])
        if not isinstance(items, list):
            raise ValueError('findings must be lists')
        findings[category] = []
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get('claim'), str)
                    or not item['claim'].strip() or item.get('kind') not in ('observed', 'inference')):
                raise ValueError('finding requires claim and observed/inference kind')
            ids, valid = references(item)
            findings[category].append(dict(item, evidence=ids, evidence_current=valid))
    questions = report.get('questions', [])
    if not isinstance(questions, list) or any(not isinstance(q, dict)
            or any(not isinstance(q.get(k), str) or not q[k].strip()
                   for k in ('question', 'next_check')) for q in questions):
        raise ValueError('questions require a question and targeted next_check')
    unknowns = [k for k, v in classification.items() if v['level'] == 'unknown']
    unknowns += ['risk_facets.' + k for k, v in facets.items() if v['level'] == 'unknown']
    for category, items in findings.items():
        if any(not item['evidence_current'] for item in items):
            unknowns.append('findings.' + category)
    if not findings['existing'] and not findings['missing']:
        unknowns.append('capability')
    if not findings['dependencies']:
        unknowns.append('dependencies')
    if questions:
        unknowns.append('open_questions')
    high = [k for k, v in facets.items() if v['level'] == 'high']
    # Keep implementation complexity separate from risk; a high money facet must
    # not be cancelled by an accidentally low aggregate risk classification.
    if high and classification['risk']['level'] != 'high':
        unknowns.append('risk_contradiction')
    budget = report.get('investigation', {})
    if not isinstance(budget, dict):
        raise ValueError('investigation must be an object')
    for key in ('steps', 'seconds', 'limit_steps', 'limit_seconds'):
        value = budget.get(key)
        if value is not None and (type(value) not in (int, float) or not 0 <= value < float('inf')):
            raise ValueError('investigation counters must be finite nonnegative numbers or null')
        if key.startswith('limit_') and key in budget and (value is None or value == 0):
            raise ValueError('investigation limits must be positive finite numbers')
    exhausted = (budget.get('steps') is not None and budget['steps'] >= budget.get('limit_steps', 8)
                 or budget.get('seconds') is not None and budget['seconds'] >= budget.get('limit_seconds', 180))
    deeper = bool(unknowns) or classification['uncertainty']['level'] == 'high'
    return {'schema_version': 2, 'classification': classification, 'risk_facets': facets,
            'findings': findings, 'questions': questions, 'evidence': checked,
            'unknowns': unknowns, 'risks': high, 'discovery': 'deeper' if deeper else 'sufficient',
            'next_action': ('report_unknowns' if exhausted else 'targeted_check') if deeper else 'stop',
            'investigation': dict(budget, exhausted=bool(exhausted)),
            'evidence_semantics': 'File freshness checked where roots supplied; claims and test results remain agent observations.'}
