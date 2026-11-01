"""Advisory workflow policy. No LLM, dispatch, persistence, or implicit account query."""
from __future__ import annotations
import impact
import quota

AUDIT_RISKS = {'security', 'money', 'persistence', 'concurrency', 'contracts'}
DELEGATION_REASONS = {'independent_work', 'multi_repo', 'capability', 'recovery'}


def recommend(report, current_agent, current_model='', limits=None, override=None, roots=None):
    assessment = impact.assess(report, roots)
    limits = limits or {}
    override = override or {}
    if not isinstance(override, dict) or set(override) - {'plan', 'execution', 'audit'}:
        raise ValueError('override supports plan, execution and audit only')
    for key in ('plan', 'audit'):
        if key in override and not isinstance(override[key], bool):
            raise ValueError('%s override must be boolean' % key)
    risks = set(assessment['risks'])
    v2 = assessment['schema_version'] == 2
    classification = assessment.get('classification', {})
    high = lambda key: classification.get(key, {}).get('level') == 'high'
    plan = (high('planning') or high('implementation')) if v2 else bool(
        risks & {'scope', 'implementation', 'contracts', 'persistence', 'uncertainty'})
    audit = bool(risks & AUDIT_RISKS) or (v2 and (high('risk') or high('independent_verification')))
    execution = 'native'
    reasons = ['Continue in the current session unless delegation has an evidenced benefit.']
    delegation = report.get('delegation', {})
    if not isinstance(delegation, dict):
        raise ValueError('delegation must be an object')
    evidence = delegation.get('evidence', [])
    if not isinstance(evidence, list) or any(not isinstance(e, str) or not e.strip() for e in evidence):
        raise ValueError('delegation evidence must contain nonempty references')
    current_evidence = bool(evidence) and (not v2 or all(
        assessment['evidence'].get(ref, {}).get('status') == 'current' for ref in evidence))
    if delegation.get('reason') in DELEGATION_REASONS and current_evidence:
        execution = 'ship' if delegation['reason'] in {'independent_work', 'multi_repo', 'recovery'} else 'quick'
        reasons.append('Delegation justified by %s: %s' % (delegation['reason'], '; '.join(evidence)))
    elif delegation:
        reasons.append('Delegation not supported by a recognized benefit and current evidence; keep native.')
    if 'execution' in override:
        if override['execution'] not in ('native', 'quick', 'ship'):
            raise ValueError('execution must be native, quick or ship')
        execution = override['execution']
        reasons.append('Execution explicitly overridden by the user.')
    plan = override.get('plan', plan) or execution == 'ship'
    # An override changes routing, never silently waives an independent risk review.
    audit = override.get('audit', audit) or audit
    providers = []
    for provider in ('claude', 'codex'):
        providers.append({'provider': provider, 'quota': quota.availability(limits, provider)})
    eligible = [p['provider'] for p in providers if p['quota'] == 'available']
    status = 'investigate' if assessment['discovery'] == 'deeper' else 'ready'
    if execution == 'native' and quota.availability(limits, current_agent) == 'limited':
        status = 'capacity_required'
        reasons.append('The current provider is limited; refresh capacity before continuing. No automatic delegation.')
    if execution != 'native' and not eligible:
        status = 'capacity_required'
        reasons.append('No measured provider capacity: do not dispatch; refresh quotas or continue natively if sufficient.')
    if assessment['unknowns']:
        reasons.append('Resolve impact unknowns before treating this as an implementation-ready route.')
    blockers = []
    if assessment['discovery'] == 'deeper':
        blockers.append('impact investigation incomplete')
    if status == 'capacity_required':
        blockers.append('provider capacity required')
    if v2 and assessment['discovery'] == 'deeper':
        status = 'investigate'
    verification = ['targeted acceptance checks', 'relevant regression checks']
    if audit:
        verification.append('independent audit; state-bound verdict')
    if risks & {'persistence', 'money'}:
        verification.append('migration/rollback or monetary invariant checks, as applicable')
    decisions = []
    if v2:
        for axis in impact.CLASSIFICATIONS:
            item = classification[axis]
            decisions.append({'axis': axis, **item})
        reasons.append('Planning: %s; evidence: %s.' % ('required' if plan else 'not currently required',
                       ', '.join(classification['planning']['evidence'] + classification['implementation']['evidence'])))
        reasons.append('Independent audit: %s; risk evidence: %s.' % ('required' if audit else 'not currently required',
                       ', '.join(classification['risk']['evidence'] + classification['independent_verification']['evidence'])))
    iterations = _iterations(report.get('iteration', {}), assessment)
    if iterations['blockers']:
        blockers.extend(iterations['blockers'])
        if status == 'ready':
            status = 'iteration_constraints_required'
    strategies = (['plan'] if plan else []) + [execution] + (['audit'] if audit else [])
    if iterations['mode'] != 'native':
        strategies.append(iterations['mode'])
    return {'schema_version': 2 if v2 else 1, 'status': status, 'assessment': assessment,
            'current_agent': {'provider': current_agent, 'model': current_model},
            'plan': plan, 'execution': execution, 'audit': audit,
            'iterations': iterations, 'strategies': strategies, 'blockers': blockers,
            'decisions': decisions,
            'providers': providers, 'eligible_providers': eligible,
            'verification': verification, 'reasons': reasons, 'override': dict(override),
            'reconsider_on': ['new dependency', 'new risk evidence', 'contract or scope change',
                              'verification failure', 'quota change', 'cited file changed']}


def _iterations(request, assessment):
    """Describe an explicitly requested bounded host mechanism; never start it."""
    if not isinstance(request, dict) or type(request.get('requested', False)) is not bool:
        raise ValueError('iteration must be an object with boolean requested')
    result = {'mode': 'native', 'new_loop': False, 'requested': request.get('requested', False),
              'blockers': [], 'stop': ['success criteria met', 'repeated failure without progress',
              'environment or flaky failure needs classification', 'budget exhausted']}
    if not result['requested']:
        return result
    mode = request.get('mode')
    if mode not in ('loop', 'goal'):
        raise ValueError('iteration mode must be loop or goal')
    if type(request.get('repeated_without_progress', False)) is not bool:
        raise ValueError('repeated_without_progress must be boolean')
    for key in ('max_attempts', 'max_seconds'):
        value = request.get(key)
        if type(value) is not int or value <= 0:
            result['blockers'].append('iteration requires positive integer ' + key)
    if not isinstance(request.get('success_check'), str) or not request['success_check'].strip():
        result['blockers'].append('iteration requires a concrete success check')
    failure = request.get('failure_kind')
    if failure not in (None, 'code') or request.get('repeated_without_progress', False):
        result['blockers'].append('classify or resolve failure before repeating')
    result['constraints'] = {k: request.get(k) for k in ('max_attempts', 'max_seconds', 'success_check', 'failure_kind')}
    if not result['blockers'] and assessment['discovery'] == 'sufficient':
        result['mode'] = mode
    return result
