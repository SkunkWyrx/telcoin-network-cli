#!/bin/bash
set -e

GITHUB_USERNAME=${GITHUB_USERNAME:-"yourusername"}
IMAGE_NAME="telcoin-observer"
VERSION=${1:-"latest"}

if [ -z "$GITHUB_TOKEN" ]; then
  echo "❌ Error: GITHUB_TOKEN environment variable not set"
  echo "Create a token at: https://github.com/settings/tokens"
  echo "Required permissions: write:packages, read:packages"
  exit 1
fi

echo "Building image..."
docker build -t ${IMAGE_NAME}:${VERSION} -f src/docker/Dockerfile .

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
