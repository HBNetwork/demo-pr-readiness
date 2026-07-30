from pr_fixture import PullRequestFacts, ready_for_release, required_approvals


def test_installed_package_exposes_readiness_policy() -> None:
    facts = PullRequestFacts(
        draft=False,
        approvals=required_approvals("normal"),
        checks_green=True,
        mergeable=True,
    )
    assert ready_for_release(facts)
