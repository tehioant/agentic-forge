"""Exact Discord thread metadata validation; authenticity belongs to trusted intake."""
import re


def validate_origin(origin):
    """Reject ambiguous metadata with ValueError; leave caller error codes unchanged."""
    identifiers = ('chat_id', 'thread_id', 'parent_chat_id', 'scope_id')
    if not isinstance(origin, dict) or set(origin) != {'platform', *identifiers} or origin.get('platform') != 'discord':
        raise ValueError('Provide the verified originating Discord thread metadata.')
    if (any(not isinstance(origin[key], str) or re.fullmatch(r'[1-9][0-9]*', origin[key]) is None
            for key in identifiers) or origin['chat_id'] != origin['thread_id']):
        raise ValueError('Originating thread identifiers must be exact and consistent.')
