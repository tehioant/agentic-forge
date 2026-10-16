"""Opt-in native Hermes plugin entrypoint; no automatic installation or inference."""
from factory_v1.conversation_binding import ConversationBinding
from factory_v1.repositories import GitHub


def register(ctx):
    binding = ctx.get_config('binding')
    enrollment_config = ctx.get_config('enrollment')
    enrollment = None
    if enrollment_config is not None:
        from factory_v1.conversation_enrollment import ConversationEnrollment
        enrollment = ConversationEnrollment(ctx, enrollment_config)
        enrollment.bound_planning = binding
        enrollment.register()
    if binding is None:
        if enrollment is None:
            raise ValueError('Configure enrollment or an existing approved planning binding.')
        return enrollment
    api_base = ctx.get_config('api_base')
    # Legacy readback is credential-blind. The strict publisher separately loads
    # its private exact host authorization; no ambient credential discovery.
    github = GitHub(api_base, 'credential-blind-controller', 10)
    native = ConversationBinding(ctx, binding, github, ctx.get_config('publication_config')).register()
    wakeup_config = ctx.get_config('wakeup_config')
    if wakeup_config is not None:
        from factory_v1.planning_wakeup import NativeWakeup
        native.wakeup = NativeWakeup(native, wakeup_config).start()
    return native
