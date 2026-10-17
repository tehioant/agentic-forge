"""Controller command grammar; parsing grants no execution authority."""

import argparse
import os


def build_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--state', required=True)
    parser.add_argument('--operator-id', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    register = commands.add_parser('register')
    register.add_argument('--request', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--project', required=True)
    inspect.add_argument('--iteration', required=True)
    for name in ('pause', 'emergency-stop', 'resume', 'inspect-control'):
        control = commands.add_parser(name, help='Durable operator interruption; no rollback or automatic restart.')
        control.add_argument('--project', required=True)
        control.add_argument('--iteration', required=True)
        if name != 'inspect-control':
            control.add_argument('--reference', required=True)
        if name == 'resume':
            control.add_argument('--api-base', required=True)
            control.add_argument('--bearer', default='credential-blind-controller')
            control.add_argument('--timeout', type=float, default=10)
    restart = commands.add_parser('reconcile-restart', help='Exact read-only-effect reconciliation; never resumes pause or sends notifications.')
    for option in ('project', 'iteration', 'api-base'):
        restart.add_argument('--' + option, required=True)
    restart.add_argument('--bearer', default='credential-blind-controller')
    restart.add_argument('--timeout', type=float, default=10)
    restart.add_argument('--recover-assignment')
    restart.add_argument('--run')
    restart.add_argument('--reference')
    onboarding = commands.add_parser('onboard')
    onboarding.add_argument('--project', required=True)
    onboarding.add_argument('--iteration', required=True)
    onboarding.add_argument('--api-base', required=True)
    onboarding.add_argument('--bearer', default='credential-blind-controller')
    onboarding.add_argument('--timeout', type=float, default=10)
    onboarding.add_argument('--creation-receipt', help='Trusted controller receipt for explicit lost-state recovery; never worker-supplied.')
    planning = commands.add_parser('plan')
    planning.add_argument('--project', required=True)
    planning.add_argument('--iteration', required=True)
    planning.add_argument('--request', required=True)
    completion = commands.add_parser('complete-planning')
    completion.add_argument('--project', required=True)
    completion.add_argument('--iteration', required=True)
    completion.add_argument('--request', required=True)
    completion.add_argument('--api-base', required=True)
    completion.add_argument('--bearer', default='credential-blind-controller')
    completion.add_argument('--timeout', type=float, default=10)
    grant = commands.add_parser('spend-grant', help='Record trusted scoped model allowance or approval.')
    grant.add_argument('--project', required=True)
    grant.add_argument('--iteration', required=True)
    grant.add_argument('--provider', required=True)
    grant.add_argument('--model', required=True)
    grant.add_argument('--operation', required=True)
    grant.add_argument('--kind', choices=['allowance', 'approval'], required=True)
    grant.add_argument('--ceiling', type=int, required=True)
    grant.add_argument('--expires', type=int, required=True, help='Unix epoch seconds.')
    grant.add_argument('--reference', required=True)
    call = commands.add_parser('model-call', help='Call the credential-blind fixed-scope Unix-socket broker.')
    call.add_argument('--socket', required=True)
    call.add_argument('--operation-id', required=True)
    call.add_argument('--reserve', type=int, required=True)
    call.add_argument('--request', required=True)
    reconcile_cmd = commands.add_parser('spend-reconcile', help='Trusted controller reconciliation for an uncertain operation.')
    for option in ['project', 'iteration', 'provider', 'model', 'operation', 'operation-id', 'outcome', 'reference']:
        reconcile_cmd.add_argument('--' + option, required=True, choices=['complete', 'failed'] if option == 'outcome' else None)
    reconcile_cmd.add_argument('--actual-units', type=int)
    resume_cmd = commands.add_parser('spend-resume', help='Resume quota only with trusted restoration evidence, never purchase capacity.')
    for option in ['project', 'iteration', 'provider', 'model', 'operation', 'reference']:
        resume_cmd.add_argument('--' + option, required=True)
    broker = commands.add_parser('model-broker', help='Run a bounded fixed-scope credential-blind model capability.')
    broker.add_argument('--socket', required=True)
    broker.add_argument('--project', required=True)
    broker.add_argument('--iteration', required=True)
    broker.add_argument('--provider', required=True)
    broker.add_argument('--model', required=True)
    broker.add_argument('--operation', required=True)
    route = broker.add_mutually_exclusive_group(required=True)
    route.add_argument('--fixture-url')
    route.add_argument('--subscription-socket')
    broker.add_argument('--reservation', type=int, default=3, help='Trusted fixture upper bound; subscription requests always reserve 1.')
    broker.add_argument('--ttl', type=int, default=300)
    broker.add_argument('--max-calls', type=int, default=100)
    broker.add_argument('--worker-uid', type=int, default=os.getuid())
    broker.add_argument('--timeout', type=float, default=10)
    for name in ('synthesize-tickets', 'complete-tickets', 'publish-tickets', 'frontier', 'reserve-ticket',
                 'synthesize-bug', 'complete-bug', 'publish-bug'):
        control = commands.add_parser(name)
        control.add_argument('--project', required=True)
        control.add_argument('--iteration', required=True)
        control.add_argument('--api-base', required=True)
        control.add_argument('--bearer', default='credential-blind-controller')
        control.add_argument('--timeout', type=float, default=10)
        if name in {'synthesize-tickets', 'complete-tickets', 'synthesize-bug', 'complete-bug', 'publish-bug'}:
            control.add_argument('--request', required=True)
        if name == 'reserve-ticket':
            control.add_argument('--issue', type=int, required=True)
    for name in ('record-requirement-change', 'resolve-requirement-change', 'resynthesize-requirements',
                 'publish-requirement-change', 'resume-requirement-change'):
        control = commands.add_parser(name, help='Scoped material revisions, selected synthesis, exact publication and explicit resume.')
        for option in ('project', 'iteration', 'request', 'api-base'):
            control.add_argument('--' + option, required=True)
        control.add_argument('--bearer', default='credential-blind-controller')
        control.add_argument('--timeout', type=float, default=10)
        if name == 'resynthesize-requirements':
            control.add_argument('--synthesis-config', required=True, help='Private independently verified host synthesis capability.')
    for name in ('declare-stuck', 'prepare-diagnosis', 'request-direction', 'apply-direction'):
        control = commands.add_parser(name, help='Explicit stuckness, read-only debug and exact operator direction.')
        for option in ('project', 'iteration', 'request', 'api-base'):
            control.add_argument('--' + option, required=True)
        control.add_argument('--bearer', default='credential-blind-controller')
        control.add_argument('--timeout', type=float, default=10)
        if name == 'prepare-diagnosis':
            control.add_argument('--dry-run', action='store_true')
        if name == 'request-direction':
            control.add_argument('--diagnosis-config', help='Private host-only independent debug execution authorization.')
    for name in ('prepare-review', 'prepare-corrections', 'review-candidate'):
        control = commands.add_parser(name, help='Exact two-axis review/corrections; no merge or closure.')
        for option in ('project', 'iteration', 'request', 'api-base'):
            control.add_argument('--' + option, required=True)
        control.add_argument('--bearer', default='credential-blind-controller')
        control.add_argument('--timeout', type=float, default=10)
        if name == 'review-candidate':
            control.add_argument('--review-config', help='Private host-only independent execution/policy authorization.')
        else:
            control.add_argument('--dry-run', action='store_true')
    for name in ['prepare-assignment', 'prepare-simplification', 'inspect-assignment', 'assignment-result', 'launch-assignment', 'stop-assignment', 'reconcile-assignment']:
        control = commands.add_parser(name, help='Bounded role assignment and trusted full-process sandbox lifecycle.')
        control.add_argument('--project', required=True)
        control.add_argument('--iteration', required=True)
        if name in {'prepare-assignment', 'prepare-simplification', 'assignment-result'}:
            control.add_argument('--request', required=True)
            control.add_argument('--api-base', required=True)
            control.add_argument('--bearer', default='credential-blind-controller')
            control.add_argument('--timeout', type=float, default=10)
        if name not in {'prepare-assignment', 'prepare-simplification'}:
            control.add_argument('--assignment', required=True)
        if name in {'prepare-assignment', 'prepare-simplification'}:
            control.add_argument('--dry-run', action='store_true')
        if name == 'launch-assignment':
            control.add_argument('--isolated', action='store_true', help='Untrusted assertion; never enables launch.')
            control.add_argument('--launcher-config', help='Private trusted controller configuration, not worker input.')
            control.add_argument('--api-base')
            control.add_argument('--bearer', default='credential-blind-controller')
            control.add_argument('--timeout', type=float, default=10)
    for name in ['attention', 'notifications', 'deliver', 'respond', 'attention-reconcile']:
        attention_command = commands.add_parser(name, help='Trusted exact-origin attention lifecycle.')
        attention_command.add_argument('--project', required=True)
        attention_command.add_argument('--iteration', required=True)
        if name in {'attention', 'respond', 'attention-reconcile'}:
            attention_command.add_argument('--request', required=True)
        if name == 'deliver':
            attention_command.add_argument('--event', required=True)
            attention_command.add_argument('--transport-config')
    publication = commands.add_parser('publish-candidate', help='Host-only exact candidate publication; never merge or close.')
    for option in ('project', 'iteration', 'assignment', 'publication-config'):
        publication.add_argument('--' + option, required=True)
    publication.add_argument('--plan-only', action='store_true', help='Read-only deterministic candidate plan, not publication approval.')
    inspection = commands.add_parser('inspect-merge', help='Inspect exact durable merge/refusal evidence, including obsolete revisions.')
    for option in ('project', 'iteration', 'assignment'):
        inspection.add_argument('--' + option, required=True)
    for name in ('merge-preflight', 'merge-candidate', 'verify-delivery'):
        control = commands.add_parser(name, help='Host-only gated merge and exact integrated delivery verification.')
        for option in ('project', 'iteration', 'merge-config'):
            control.add_argument('--' + option, required=True)
    for name in ('observe-delivery', 'watch-delivery', 'inspect-delivery'):
        control = commands.add_parser(name, help='Host-only deterministic main CI/CD and pinned deployment health.')
        for option in ('project', 'iteration'):
            control.add_argument('--' + option, required=True)
        if name != 'inspect-delivery':
            control.add_argument('--delivery-config', required=True, help='Private operator-approved pinned product contract.')
        if name == 'watch-delivery':
            control.add_argument('--samples', type=int, default=100, help='Bounded repeated observations (1..10000).')
            control.add_argument('--interval', type=float, default=30, help='Seconds between observations (1..3600).')
    for name in ('complete-iteration', 'next-iteration'):
        control = commands.add_parser(name, help='Verified milestone report/wait or explicit same-product interview input.')
        for option in ('project', 'iteration', 'request'):
            control.add_argument('--' + option, required=True)
        if name == 'complete-iteration':
            control.add_argument('--api-base', required=True)
            control.add_argument('--bearer', default='credential-blind-controller')
            control.add_argument('--timeout', type=float, default=10)
    for name in ('register-recovery', 'continue-recovery', 'prepare-recovery', 'verify-recovery', 'inspect-recovery'):
        control = commands.add_parser(name, help='Host-owned incident recovery through unchanged worker/review/delivery gates.')
        for option in ('project', 'iteration'):
            control.add_argument('--' + option, required=True)
        if name != 'inspect-recovery':
            control.add_argument('--api-base', required=True)
            control.add_argument('--bearer', default='credential-blind-controller')
            control.add_argument('--timeout', type=float, default=10)
        if name in {'register-recovery', 'continue-recovery', 'verify-recovery'}:
            control.add_argument('--merge-config' if name == 'verify-recovery' else '--recovery-config', required=True)
        if name == 'prepare-recovery':
            control.add_argument('--request', required=True)
            control.add_argument('--dry-run', action='store_true')
    return parser
