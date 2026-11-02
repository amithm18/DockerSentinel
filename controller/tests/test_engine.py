"""End-to-end engine behaviour against a fake cluster (no Kubernetes needed)."""
import copy

from docksentinel import actions
from docksentinel.diagnosis import load_rules
from docksentinel.engine import Engine, Settings
from docksentinel.store import Store
from podfactory import make_deployment, make_pod, make_rs


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeKube:
    """Simulates just enough of the Kubernetes API for the engine."""

    def __init__(self, dep, rs_list, heal_on_change=True):
        self.dep, self.rs_list, self.heal = dep, rs_list, heal_on_change
        self.calls = []

    def owner_deployment(self, ns, rs):
        return self.dep["metadata"]["name"], int(
            next(r for r in self.rs_list if r["metadata"]["name"] == rs)
            ["metadata"]["annotations"]["deployment.kubernetes.io/revision"])

    def deployment(self, ns, name):
        return copy.deepcopy(self.dep)

    def deployments(self, ns):
        return [copy.deepcopy(self.dep)]

    def replicasets(self, ns, name):
        return copy.deepcopy(self.rs_list)

    def pods(self, ns, selector):
        return [] if self.heal else [make_pod(waiting="CrashLoopBackOff", ready=False,
                                              last="OOMKilled", last_exit=137)]

    def logs(self, ns, pod, container):
        return "boom"

    def _new_revision(self, healthy):
        rev = actions.deployment_revision(self.dep) + 1
        self.dep = make_deployment(revision=rev, healthy=healthy,
                                   replicas=self.dep["spec"]["replicas"])

    def set_memory_limit(self, ns, name, container, limit):
        self.calls.append(("memory", limit))
        self._new_revision(self.heal)

    def set_pod_template(self, ns, name, tpl):
        self.calls.append(("rollback", tpl["spec"]["containers"][0]["image"]))
        self._new_revision(self.heal)

    def restart(self, ns, name):
        self.calls.append(("restart",))


class QuietAlerter:
    def __init__(self):
        self.sent, self.notes = [], []

    def send(self, inc, dx, out):
        self.sent.append((inc, dx, out))

    def info(self, msg):
        self.notes.append(msg)


def make_engine(kube, store=None, **kw):
    clock = FakeClock()
    eng = Engine(kube, store or Store(":memory:"), load_rules(), QuietAlerter(),
                 Settings(verify_seconds=30, **kw), clock=clock, sleep=clock.sleep)
    return eng, clock


def test_oom_is_fixed_by_raising_memory_and_verified():
    kube = FakeKube(make_deployment(name="web", revision=1, replicas=1, healthy=False),
                    [make_rs("web-rs1", 1, "app:v1")])
    eng, _ = make_engine(kube)
    pod = make_pod(rs="web-rs1", waiting="CrashLoopBackOff", ready=False,
                   last="OOMKilled", last_exit=137, memory="64Mi")
    out = eng.handle_pod(pod)
    assert kube.calls == [("memory", "128Mi")]
    assert out.status == "recovered"
    assert eng.store.good_revision("demo", "web") == 2


def test_bad_release_rolls_back_to_last_good_revision():
    kube = FakeKube(make_deployment(revision=2, healthy=False),
                    [make_rs("web-rs1", 1, "nginx:1.27-alpine", h="h1"),
                     make_rs("web-rs2", 2, "nginx:9.99-does-not-exist", h="h2")])
    store = Store(":memory:")
    store.mark_good("demo", "web", 1)
    eng, _ = make_engine(kube, store)
    pod = make_pod(rs="web-rs2", waiting="ImagePullBackOff", ready=False,
                   image="nginx:9.99-does-not-exist")
    out = eng.handle_pod(pod)
    assert kube.calls == [("rollback", "nginx:1.27-alpine")]
    assert out.status == "recovered" and "revision 2 to last good revision 1" in out.detail


def test_rollback_template_drops_pod_template_hash():
    tpl = actions.rollback_template([make_rs("rs1", 1, "img:v1", h="abc")], 1)
    assert "pod-template-hash" not in tpl["metadata"]["labels"]


def test_no_rollback_without_verified_good_revision():
    kube = FakeKube(make_deployment(revision=1, healthy=False),
                    [make_rs("web-rs1", 1, "nginx:bad")])
    eng, _ = make_engine(kube)
    out = eng.handle_pod(make_pod(rs="web-rs1", waiting="ImagePullBackOff", ready=False))
    assert kube.calls == [] and out.status == "reported"


def test_memory_leak_stops_after_two_raises():
    kube = FakeKube(make_deployment(revision=1, replicas=1, healthy=False),
                    [make_rs(f"web-rs{i}", i, "app") for i in range(1, 6)],
                    heal_on_change=False)
    eng, clock = make_engine(kube)
    outcomes = []
    for i, mem in enumerate(["64Mi", "128Mi", "256Mi"], start=1):
        clock.t += 120                      # past the cooldown
        pod = make_pod(rs=f"web-rs{i}", waiting="CrashLoopBackOff", ready=False,
                       last="OOMKilled", last_exit=137, memory=mem)
        outcomes.append(eng.handle_pod(pod))
    assert [o.status for o in outcomes] == ["failed", "failed", "reported"]
    assert "memory leak" in outcomes[-1].detail
    assert kube.calls == [("memory", "128Mi"), ("memory", "256Mi")]


def test_dry_run_never_changes_the_cluster():
    kube = FakeKube(make_deployment(revision=1, replicas=1, healthy=False),
                    [make_rs("web-rs1", 1, "app")])
    eng, _ = make_engine(kube, dry_run=True)
    out = eng.handle_pod(make_pod(rs="web-rs1", waiting="CrashLoopBackOff", ready=False,
                                  last="OOMKilled", last_exit=137, memory="64Mi"))
    assert kube.calls == [] and out.status == "dry_run"


def test_unlabelled_deployments_are_ignored():
    dep = make_deployment(revision=1, healthy=False, managed=False)
    kube = FakeKube(dep, [make_rs("web-rs1", 1, "app")])
    eng, _ = make_engine(kube)
    pod = make_pod(rs="web-rs1", waiting="CrashLoopBackOff", ready=False,
                   last="OOMKilled", last_exit=137)
    assert eng.handle_pod(pod) is None


def test_healthy_revision_learned_after_verify_window():
    kube = FakeKube(make_deployment(revision=1, healthy=True), [])
    eng, clock = make_engine(kube)
    eng.refresh_good_revisions("demo")
    assert eng.store.good_revision("demo", "web") is None
    clock.t += 31
    eng.refresh_good_revisions("demo")
    assert eng.store.good_revision("demo", "web") == 1


def test_memory_parsing():
    assert actions.next_memory_limit("64Mi") == "128Mi"
    assert actions.next_memory_limit("768Mi") == "1024Mi"
    assert actions.next_memory_limit("1Gi") is None
    assert actions.parse_memory("1G") == 10**9
