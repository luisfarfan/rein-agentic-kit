"""Advisory workflow policy. No LLM, dispatch, persistence, or implicit account query."""
from __future__ import annotations
import impact
import quota

AUDIT_RISKS = {'security', 'money', 'persistence', 'concurrency', 'contracts'}
DELEGATION_REASONS = {'independent_work', 'multi_repo', 'capability', 'recovery'}


def recommend(report, current_agent, current_model='', limits=None, override=None):
    assessment = impact.assess(report)
    limits = limits or {}
    override = override or {}
    if not isinstance(override, dict) or set(override) - {'plan', 'execution', 'audit'}:
        raise ValueError('override supports plan, execution and audit only')
    for key in ('plan', 'audit'):
        if key in override and not isinstance(override[key], bool):
            raise ValueError('%s override must be boolean' % key)
    risks = set(assessment['risks'])
    plan = bool(risks & {'scope', 'implementation', 'contracts', 'persistence'})
    audit = bool(risks & AUDIT_RISKS)
    execution = 'native'
    reasons = ['Continue in the current session unless delegation has an evidenced benefit.']
    delegation = report.get('delegation', {})
    if not isinstance(delegation, dict):
        raise ValueError('delegation must be an object')
    evidence = delegation.get('evidence', [])
    if not isinstance(evidence, list) or any(not isinstance(e, str) or not e.strip() for e in evidence):
        raise ValueError('delegation evidence must contain nonempty references')
    if delegation.get('reason') in DELEGATION_REASONS and evidence:
        execution = 'ship' if delegation['reason'] in {'independent_work', 'multi_repo', 'recovery'} else 'quick'
        reasons.append('Delegation justified by %s: %s' % (delegation['reason'], '; '.join(evidence)))
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
        block = limits.get(provider) or {}
        limited = bool(quota.brake_reason(limits, provider))
        known = any(block.get(w) is not None for w in quota.WINDOWS)
        providers.append({'provider': provider, 'quota': 'limited' if limited else 'available' if known else 'unknown'})
    eligible = [p['provider'] for p in providers if p['quota'] == 'available']
    status = 'investigate' if assessment['unknowns'] else 'ready'
    if execution != 'native' and not eligible:
        status = 'capacity_required'
        reasons.append('No measured provider capacity: do not dispatch; refresh quotas or continue natively if sufficient.')
    if assessment['unknowns']:
        reasons.append('Resolve impact unknowns before treating this as an implementation-ready route.')
    verification = ['targeted acceptance checks', 'relevant regression checks']
    if audit:
        verification.append('independent audit; state-bound verdict')
    if risks & {'persistence', 'money'}:
        verification.append('migration/rollback or monetary invariant checks, as applicable')
    return {'schema_version': 1, 'status': status, 'assessment': assessment,
            'current_agent': {'provider': current_agent, 'model': current_model},
            'plan': plan, 'execution': execution, 'audit': audit,
            'iterations': {'mode': 'native', 'new_loop': False,
                           'stop': ['success criteria met', 'repeated failure without progress',
                                    'environment or flaky failure needs classification', 'budget exhausted']},
            'providers': providers, 'eligible_providers': eligible,
            'verification': verification, 'reasons': reasons, 'override': dict(override),
            'reconsider_on': ['new dependency', 'new risk evidence', 'contract or scope change',
                              'verification failure', 'quota change']}
