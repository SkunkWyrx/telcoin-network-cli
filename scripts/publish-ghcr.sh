#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

GITHUB_USERNAME=${GITHUB_USERNAME:-"yourusername"}
IMAGE_NAME="telcoin-observer"
VERSION=${1:-"latest"}
GIT_BRANCH=${GIT_BRANCH:-"main"}
GIT_TAG=${GIT_TAG-v0.16.0-adiri}
GIT_REPO_URL=${GIT_REPO_URL:-"https://github.com/telcoin-association/telcoin-network.git"}

if [ -z "$GITHUB_TOKEN" ]; then
  echo "❌ Error: GITHUB_TOKEN environment variable not set"
  echo "Create a token at: https://github.com/settings/tokens"
  echo "Required permissions: write:packages, read:packages"
  exit 1
fi

echo "Building image..."
if [[ -n "${GIT_TAG}" ]]; then
  echo "Using git tag: ${GIT_TAG}"
else
  echo "GIT_TAG is empty; using git branch: ${GIT_BRANCH}"
fi
docker build \
  --build-arg GIT_BRANCH="${GIT_BRANCH}" \
  --build-arg GIT_TAG="${GIT_TAG}" \
  --build-arg GIT_REPO_URL="${GIT_REPO_URL}" \
  -t ${IMAGE_NAME}:${VERSION} \
  -f src/docker/Dockerfile \
  .

echo "Tagging image..."
docker tag ${IMAGE_NAME}:${VERSION} ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:${VERSION}
if [ "${VERSION}" != "latest" ]; then
  docker tag ${IMAGE_NAME}:${VERSION} ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:latest
fi

echo "Logging in to GHCR..."
echo $GITHUB_TOKEN | docker login ghcr.io -u ${GITHUB_USERNAME} --password-stdin

echo "Pushing to GHCR..."
docker push ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:${VERSION}
if [ "${VERSION}" != "latest" ]; then
  docker push ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:latest
fi

echo "✅ Published ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:${VERSION}"
if [ "${VERSION}" != "latest" ]; then
  echo "✅ Also published ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:latest"
fi
