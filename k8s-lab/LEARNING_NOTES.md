# Kubernetes Learning Notes (Day 1 glossary)

Every command below works in this lab. Add `-n lab` to talk to our namespace.

| Concept | What it is | See it |
|---|---|---|
| **Cluster** | A group of machines run together by Kubernetes. Ours is made by kind, and each "machine" is a Docker container. | `kubectl cluster-info` |
| **Node** | One machine in the cluster. The *control plane* node runs the brain (API server, scheduler, etcd); the *worker* runs our apps. | `kubectl get nodes -o wide` |
| **Namespace** | A named folder for objects. Lab objects live in `lab`, system ones in `kube-system`. | `kubectl get ns` |
| **Pod** | The smallest thing Kubernetes runs: one or more containers sharing an IP. Pods are disposable and get replaced, not repaired. | `kubectl -n lab get pods` |
| **Deployment** | Your desired state: "run N copies of this pod template". It handles updates and rollbacks. | `kubectl -n lab get deploy` |
| **ReplicaSet** | Created by a Deployment for each version of the template; keeps exactly N pods alive. Old ones are kept (at 0 pods) for rollback. | `kubectl -n lab get rs` |
| **Service** | A stable name and IP in front of a set of pods (picked by label). db-app reaches the database at `postgres:5432`. | `kubectl -n lab get svc` |
| **Secret** | Holds sensitive config (passwords, URLs). Injected into containers as env vars or files. | `kubectl -n lab get secret db-creds -o yaml` |
| **Requests / limits** | *Request* = what the scheduler reserves for a container. *Limit* = hard ceiling. Over the memory limit → killed; over the CPU limit → slowed. | `kubectl -n lab describe pod <pod>` (Limits/Requests) |
| **Readiness probe** | "Can this pod take traffic?" If it fails, the pod is removed from its Service but **not** restarted. | `READY` column in `kubectl -n lab get pods` |
| **Liveness probe** | "Is this container still alive?" If it fails repeatedly, the container is restarted. | events: `Liveness probe failed` |
| **Rolling update** | Replacing old pods with new ones gradually. With `maxUnavailable: 0` an old pod stays until a new one is Ready, so a bad release never takes the app down. | `kubectl -n lab rollout status deploy/bad-release` |
| **Rollout history / undo** | Each template change is a *revision*. `undo` re-applies the previous revision's template, which gets a **new** revision number. | `kubectl -n lab rollout history deploy/bad-release`, `kubectl -n lab rollout undo deploy/bad-release` |
| **OOMKilled** | The container used more memory than its limit, so the kernel killed it. Exit code 137 (= 128 + signal 9). Only visible in pod status, not events. | `kubectl -n lab get pod <pod> -o jsonpath='{.status.containerStatuses[0].lastState}'` |
| **CrashLoopBackOff** | A *state*, not a cause: "this container keeps crashing; waiting before the next restart" (10 s, 20 s, 40 s … max 5 min). The cause is in the exit code and previous logs. | `kubectl -n lab logs <pod> --previous` |
| **ImagePullBackOff / ErrImagePull** | The node cannot get the image (wrong tag, no access, not loaded into kind). Container never starts, so no logs. | `kubectl -n lab describe pod <pod>` (Events) |
| **CreateContainerConfigError** | The container cannot be created because its config is invalid, e.g. a referenced Secret/ConfigMap is missing. Fixes itself once the object exists. | `kubectl -n lab describe pod <pod>` |
| **Events** | Short-lived (≈1 h) messages from Kubernetes about what it did: scheduled, pulled, killed, probe failed, back-off. | `kubectl -n lab get events --sort-by=.lastTimestamp` |
| **Exit code** | The number a process returns when it stops. 0 = clean, 1 = app error (our crashes), 137 = killed by SIGKILL (OOM). | `scripts/status.sh` (EXIT column) |
| **kind load** | kind nodes have their own image store and cannot see your laptop's Docker images. This copies images in. | `docker exec docksentinel-worker crictl images` |
| **imagePullPolicy: IfNotPresent** | Use the image already on the node; download only if missing. Never use `:latest`, which defaults to "always pull". | `kubectl -n lab get deploy memory-hog -o yaml` |
