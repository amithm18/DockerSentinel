"""Each Kubernetes failure must map to the right cause and action."""
import pytest

from docksentinel.collector import build_incident, failing_container
from docksentinel.diagnosis import diagnose, load_rules
from podfactory import make_pod

RULES = load_rules()


def dx_for(pod, logs="", new_revision=False):
    inc = build_incident(pod, deployment="web", revision=2, logs=logs)
    assert inc is not None, "pod should be detected as failing"
    return inc, diagnose(inc, RULES, new_revision=new_revision)


def test_healthy_pod_is_ignored():
    assert failing_container(make_pod()) is None
    assert build_incident(make_pod(), "web", 1) is None


def test_starting_pod_is_not_a_failure():
    assert failing_container(make_pod(waiting="ContainerCreating", ready=False)) is None


def test_oom_raises_memory():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="OOMKilled",
                   last_exit=137, memory="64Mi", restarts=3)
    inc, dx = dx_for(pod)
    assert dx.rule == "out_of_memory" and dx.action == "raise_memory"
    assert "64Mi" in dx.cause
    assert inc.signal == "CrashLoopBackOff, OOMKilled, exit 137"


def test_oom_wins_even_on_new_revision():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="OOMKilled", last_exit=137)
    assert dx_for(pod, new_revision=True)[1].rule == "out_of_memory"


@pytest.mark.parametrize("reason", ["ImagePullBackOff", "ErrImagePull"])
def test_bad_image_rolls_back(reason):
    pod = make_pod(waiting=reason, ready=False, image="nginx:9.99-does-not-exist")
    _, dx = dx_for(pod)
    assert dx.rule == "bad_image" and dx.action == "rollback"
    assert "nginx:9.99-does-not-exist" in dx.cause


def test_missing_secret_is_report_only():
    pod = make_pod(waiting="CreateContainerConfigError", ready=False,
                   waiting_msg='secret "api-secret" not found')
    _, dx = dx_for(pod)
    assert dx.rule == "missing_config" and dx.action == "report"
    assert 'secret "api-secret" not found' in dx.cause


def test_crash_after_new_release_rolls_back():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="Completed", last_exit=0)
    _, dx = dx_for(pod, new_revision=True)
    assert dx.rule == "bad_release" and dx.action == "rollback"


def test_crash_on_old_revision_is_reported_not_rolled_back():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="Error", last_exit=1)
    _, dx = dx_for(pod, new_revision=False)
    assert dx.rule == "crash_loop" and dx.action == "report"


def test_database_down_detected_from_logs():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="Error", last_exit=1)
    logs = "Error: connect ECONNREFUSED 10.96.0.12:5432\n    at TCPConnectWrap"
    _, dx = dx_for(pod, logs=logs, new_revision=True)
    assert dx.rule == "dependency_down" and dx.action == "report"


def test_missing_module_reported():
    pod = make_pod(waiting="CrashLoopBackOff", ready=False, last="Error", last_exit=1)
    _, dx = dx_for(pod, logs="Error: Cannot find module './routes'")
    assert dx.rule == "missing_module_or_command"


def test_unknown_failure_has_safe_default():
    pod = make_pod(waiting="RunContainerError", ready=False)
    _, dx = dx_for(pod)
    assert dx.rule == "unknown" and dx.action == "report"
