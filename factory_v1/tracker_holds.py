"""Translate durable holds without requiring extra native Projects status options."""
from .planning import require


def represented_role(work, role):
    statuses = work['request']['tracker']['statuses']
    # Native boards deliberately have only ready/active/done. In Progress is
    # the visible waiting state; the correlated durable hold denies dispatch.
    if role in statuses:
        return role
    require(role in {'blocked', 'deferred'} and 'active' in statuses,
            'Tracker cannot represent this progress.', 'projects_blocked')
    return 'active'


def option(work, board, role):
    statuses = work['request']['tracker']['statuses']
    return board['status']['options'][statuses[represented_role(work, role)]]


def representation(work):
    return 'blocked-option' if 'blocked' in work['request']['tracker']['statuses'] else 'native-active-with-durable-hold'
