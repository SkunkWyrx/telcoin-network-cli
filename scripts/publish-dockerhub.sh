#!/bin/bash
set -e

DOCKER_USERNAME=${DOCKER_USERNAME:-"yourusername"}
IMAGE_NAME="telcoin-observer"
VERSION=${1:-"latest"}

echo "Building image..."
docker build -t ${IMAGE_NAME}:${VERSION} -f src/docker/Dockerfile .

echo "Tagging image..."
docker tag ${IMAGE_NAME}:${VERSION} ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}
if [ "${VERSION}" != "latest" ]; then
  docker tag ${IMAGE_NAME}:${VERSION} ${DOCKER_USERNAME}/${IMAGE_NAME}:latest
fi

echo "Pushing to Docker Hub..."
echo "Make sure you're logged in: docker login"
read -p "Press enter to continue or Ctrl+C to cancel..."

docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}
if [ "${VERSION}" != "latest" ]; then
  docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:latest
fi

echo "✅ Published ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
if [ "${VERSION}" != "latest" ]; then
  echo "✅ Also published ${DOCKER_USERNAME}/${IMAGE_NAME}:latest"
fi
