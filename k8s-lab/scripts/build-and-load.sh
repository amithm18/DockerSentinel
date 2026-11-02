#!/usr/bin/env bash
# Build the lab images and copy them into the kind cluster's nodes.
# kind nodes have their own container runtime (containerd), so they cannot see
# images in the laptop's Docker; "kind load" copies them in.
set -euo pipefail

CLUSTER=docksentinel
APPS_DIR="$(cd "$(dirname "$0")/../apps" && pwd)"

echo "==> Building lab images"
docker build -t lab/memory-hog:v1 "$APPS_DIR/memory-hog"
docker build -t lab/bad-release:v1 --build-arg APP_VERSION=v1 --build-arg BREAK=0 "$APPS_DIR/bad-release"
docker build -t lab/bad-release:v2 --build-arg APP_VERSION=v2 --build-arg BREAK=1 "$APPS_DIR/bad-release"
docker build -t lab/db-app:v1 "$APPS_DIR/db-app"

echo "==> Pulling postgres"
docker pull postgres:16-alpine

echo "==> Loading images into kind cluster '$CLUSTER'"
for image in lab/memory-hog:v1 lab/bad-release:v1 lab/bad-release:v2 lab/db-app:v1; do
  kind load docker-image "$image" --name "$CLUSTER"
done

# postgres is a multi-platform image. With Docker's containerd image store only the
# laptop's platform layers are downloaded, and "kind load docker-image" fails with
# "content digest ... not found". Export just our platform and load the archive.
TMP_TAR="$(mktemp --suffix=.tar)"
trap 'rm -f "$TMP_TAR"' EXIT
docker save --platform "linux/$(docker version -f "{{.Server.Arch}}")" postgres:16-alpine -o "$TMP_TAR"
kind load image-archive "$TMP_TAR" --name "$CLUSTER"

echo "==> Done. Images on the worker node:"
docker exec "$CLUSTER-worker" crictl images | grep -E 'lab/|postgres'
