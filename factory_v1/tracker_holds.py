"""Translate durable holds without requiring extra native Projects status options."""
from .planning import require


def option(work, board, role):
    statuses = work['request']['tracker']['statuses']
    # Native boards deliberately have only ready/active/done. In Progress is
    # the visible waiting state; the correlated durable hold denies dispatch.
    represented = role if role in statuses else 'active' if role in {'blocked', 'deferred'} else role
    require(represented in statuses, 'Tracker cannot represent this progress.', 'projects_blocked')
    return board['status']['options'][statuses[represented]]


def representation(work):
    return 'blocked-option' if 'blocked' in work['request']['tracker']['statuses'] else 'native-active-with-durable-hold'
