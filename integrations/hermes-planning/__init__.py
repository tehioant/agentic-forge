"""Opt-in native Hermes plugin entrypoint; no automatic installation or inference."""
from factory_v1.conversation_binding import ConversationBinding
from factory_v1.repositories import GitHub


def register(ctx):
    binding = ctx.get_config('binding')
    enrollment_config = ctx.get_config('enrollment')
    enrollment = None
    handoff_config = ctx.get_config('project_handoff')
    if handoff_config is not None:
        from factory_v1.conversation_enrollment import ConversationEnrollment
        from factory_v1.project_setup import ProjectSetup
        from factory_v1.project_handoff import ProjectHandoff
        if enrollment_config is None or binding is not None:
            raise ValueError('Conversational composition requires enrollment, not a second static binding.')
        enrollment = ConversationEnrollment(ctx, enrollment_config)
        setup = ProjectSetup(enrollment, ctx.get_config('project_setup')).register()
        enrollment.setup = setup
        creation = tracker = execution = decisions = None
        if ctx.get_config('project_creation') is not None:
            from factory_v1.project_creation import ProjectCreation
            creation = ProjectCreation(setup, ctx.get_config('project_creation')).register()
        if ctx.get_config('project_tracker') is not None:
            from factory_v1.project_tracker import ProjectTracker
            tracker = ProjectTracker(setup, ctx.get_config('project_tracker'))
        if ctx.get_config('project_execution') is not None:
            from factory_v1.project_execution import ProjectExecution
            if tracker is None:
                raise ValueError('Execution preparation requires the same project tracker.')
            execution = ProjectExecution(setup, tracker, ctx.get_config('project_execution'))
        if ctx.get_config('onboarding_decisions') is not None:
            from factory_v1.onboarding_decisions import OnboardingDecisions
            decisions = OnboardingDecisions(enrollment, ctx.get_config('onboarding_decisions'), setup=setup)
        return ProjectHandoff(enrollment, setup, creation, tracker, execution, decisions, handoff_config).register()
    if enrollment_config is not None:
        from factory_v1.conversation_enrollment import ConversationEnrollment
        enrollment = ConversationEnrollment(ctx, enrollment_config)
        enrollment.bound_planning = binding
        decisions_config = ctx.get_config('onboarding_decisions')
        if decisions_config is None:
            enrollment.register()
        else:
            from factory_v1.onboarding_decisions import OnboardingDecisions
            setup = None
            setup_config = ctx.get_config('project_setup')
            if setup_config is not None:
                from factory_v1.project_setup import ProjectSetup
                setup = ProjectSetup(enrollment, setup_config).register()
                enrollment.setup = setup
            enrollment.decisions = OnboardingDecisions(enrollment, decisions_config, setup=setup).register()
    elif ctx.get_config('onboarding_decisions') is not None:
        raise ValueError('Onboarding decisions require trusted conversation enrollment.')
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
