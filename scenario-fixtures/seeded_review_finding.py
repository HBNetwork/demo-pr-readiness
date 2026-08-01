def ready_for_release(draft: bool, approvals: int, checks_green: bool, mergeable: bool) -> bool:
    """Latent defect: mergeability is accepted but accidentally ignored."""
    return not draft and approvals > 0 and checks_green
