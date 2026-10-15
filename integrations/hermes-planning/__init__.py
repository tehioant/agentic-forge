"""Opt-in native Hermes plugin entrypoint; no automatic installation or inference."""
from factory_v1.conversation_binding import ConversationBinding
from factory_v1.repositories import GitHub


def register(ctx):
    binding = ctx.get_config('binding')
    api_base = ctx.get_config('api_base')
    # Readback only, as in the supervisor. Private-repository readback requires
    # the separately approved host route; no ambient credential discovery.
    github = GitHub(api_base, 'credential-blind-controller', 10)
    return ConversationBinding(ctx, binding, github).register()
