# Docker Image Distribution Guide

This guide covers multiple methods for publishing and distributing the Telcoin Network observer node Docker image.

## Distribution Options

### 1. Docker Hub (Recommended for Public Distribution)

**Pros:**
- Most widely used and recognized
- Free for public repositories
- Easy to use: `docker pull username/image:tag`
- Good for open-source projects
- No authentication needed for public images

**Cons:**
- Rate limits on free tier (200 pulls per 6 hours for anonymous)
- Less control over infrastructure

#### Setup Steps:

1. **Create Docker Hub Account**
   - Sign up at https://hub.docker.com
   - Create a repository (e.g., `telcoin-observer`)

2. **Tag and Push Image**
   ```bash
   # Build the image
   docker build -t telcoin-observer:latest -f src/docker/Dockerfile .
   
   # Tag for Docker Hub (replace 'yourusername' with your Docker Hub username)
   docker tag telcoin-observer:latest yourusername/telcoin-observer:latest
   docker tag telcoin-observer:latest yourusername/telcoin-observer:v1.0.0
   
   # Login to Docker Hub
   docker login
   
   # Push the image
   docker push yourusername/telcoin-observer:latest
   docker push yourusername/telcoin-observer:v1.0.0
   ```

3. **Users can pull with:**
   ```bash
   docker pull yourusername/telcoin-observer:latest
   ```

---

### 2. GitHub Container Registry (ghcr.io) (Recommended for GitHub Projects)

**Pros:**
- Integrated with GitHub
- Free for public repositories
- No rate limits for authenticated users
- Good for organizations
- Automatic cleanup options

**Cons:**
- Requires GitHub account
- Less familiar to some users

#### Setup Steps:

1. **Create GitHub Personal Access Token**
   - Go to GitHub Settings → Developer settings → Personal access tokens
   - Create token with `write:packages` and `read:packages` permissions

2. **Tag and Push Image**
   ```bash
   # Build the image
   docker build -t telcoin-observer:latest -f src/docker/Dockerfile .
   
   # Tag for GHCR (replace 'username' with your GitHub username)
   docker tag telcoin-observer:latest ghcr.io/username/telcoin-observer:latest
   docker tag telcoin-observer:latest ghcr.io/username/telcoin-observer:v1.0.0
   
   # Login to GHCR
   echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
   
   # Push the image
   docker push ghcr.io/username/telcoin-observer:latest
   docker push ghcr.io/username/telcoin-observer:v1.0.0
   ```

3. **Users can pull with:**
   ```bash
   docker pull ghcr.io/username/telcoin-observer:latest
   ```

---

### 3. AWS Elastic Container Registry (ECR)

**Pros:**
- Integrated with AWS services
- Good for AWS deployments
- Fine-grained access control
- Image scanning available

**Cons:**
- Requires AWS account
- Costs for storage and data transfer
- More complex setup

#### Setup Steps:

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Create repository (if needed)
aws ecr create-repository --repository-name telcoin-observer --region us-east-1

# Tag and push
docker tag telcoin-observer:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/telcoin-observer:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/telcoin-observer:latest
```

---

### 4. Google Container Registry (GCR) / Artifact Registry

**Pros:**
- Integrated with Google Cloud
- Good for GCP deployments
- Image scanning available

**Cons:**
- Requires Google Cloud account
- Costs for storage and data transfer

---

## Best Practices

### Image Tagging Strategy

Use semantic versioning and multiple tags:

```bash
# Tag with version, latest, and commit SHA
VERSION="1.0.0"
COMMIT_SHA=$(git rev-parse --short HEAD)

docker tag telcoin-observer:latest yourusername/telcoin-observer:latest
docker tag telcoin-observer:latest yourusername/telcoin-observer:v${VERSION}
docker tag telcoin-observer:latest yourusername/telcoin-observer:v${VERSION}-${COMMIT_SHA}
```

**Tag Strategy:**
- `latest` - Always points to the most recent stable release
- `v1.0.0` - Specific version (immutable)
- `v1.0.0-abc1234` - Version with commit SHA (for debugging)

### Multi-Platform Builds

For broader compatibility, build for multiple architectures:

```bash
# Install buildx (if not already installed)
docker buildx create --use

# Build for multiple platforms
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t yourusername/telcoin-observer:latest \
  -f src/docker/Dockerfile \
  --push .
```

**Note:** Multi-platform builds take longer and require more resources.

### Automated Builds with GitHub Actions

Create `.github/workflows/docker-publish.yml`:

```yaml
name: Build and Publish Docker Image

on:
  push:
    tags:
      - 'v*'
    branches:
      - main
  pull_request:
    branches:
      - main

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v2
      
      - name: Login to Docker Hub
        uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v4
        with:
          images: yourusername/telcoin-observer
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable={{is_default_branch}}
      
      - name: Build and push
        uses: docker/build-push-action@v4
        with:
          context: .
          file: ./src/docker/Dockerfile
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=registry,ref=yourusername/telcoin-observer:buildcache
          cache-to: type=registry,ref=yourusername/telcoin-observer:buildcache,mode=max
```

---

## Quick Reference: Publishing Scripts

### Script 1: Publish to Docker Hub

Create `scripts/publish-dockerhub.sh`:

```bash
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
docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}
if [ "${VERSION}" != "latest" ]; then
  docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:latest
fi

echo "Published ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
```

### Script 2: Publish to GHCR

Create `scripts/publish-ghcr.sh`:

```bash
#!/bin/bash
set -e

GITHUB_USERNAME=${GITHUB_USERNAME:-"yourusername"}
IMAGE_NAME="telcoin-observer"
VERSION=${1:-"latest"}

if [ -z "$GITHUB_TOKEN" ]; then
  echo "Error: GITHUB_TOKEN environment variable not set"
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

echo "Published ghcr.io/${GITHUB_USERNAME}/${IMAGE_NAME}:${VERSION}"
```

---

## Security Best Practices

1. **Scan Images for Vulnerabilities**
   ```bash
   # Using Trivy
   trivy image yourusername/telcoin-observer:latest
   
   # Using Docker Scout (built into Docker)
   docker scout cves yourusername/telcoin-observer:latest
   ```

2. **Use Specific Tags in Production**
   - Avoid using `latest` in production
   - Use version tags: `v1.0.0`

3. **Sign Images** (Optional but recommended)
   ```bash
   # Enable Docker Content Trust
   export DOCKER_CONTENT_TRUST=1
   docker push yourusername/telcoin-observer:v1.0.0
   ```

4. **Keep Base Images Updated**
   - Regularly rebuild images to get security updates
   - Monitor for base image vulnerabilities

---

## Cost Considerations

| Registry | Free Tier | Storage Cost | Data Transfer |
|----------|-----------|--------------|---------------|
| Docker Hub | Public repos free | Free for public | Free for public |
| GHCR | Public repos free | Free for public | Free for public |
| AWS ECR | 500MB/month free | $0.10/GB/month | $0.09/GB out |
| GCR | 0.5GB free | $0.026/GB/month | $0.12/GB out |

---

## Recommendation

**For Open Source Projects:**
- **Primary:** Docker Hub (widest adoption)
- **Secondary:** GitHub Container Registry (if using GitHub)

**For Enterprise/Private:**
- AWS ECR (if using AWS)
- Google Artifact Registry (if using GCP)
- Private Docker registry

**For Maximum Reach:**
- Publish to both Docker Hub and GHCR
- Use automated builds via GitHub Actions

---

## Example: Complete Publishing Workflow

```bash
# 1. Build locally and test
docker build -t telcoin-observer:latest -f src/docker/Dockerfile .
docker run --rm telcoin-observer:latest --help

# 2. Tag for distribution
VERSION="1.0.0"
docker tag telcoin-observer:latest yourusername/telcoin-observer:latest
docker tag telcoin-observer:latest yourusername/telcoin-observer:v${VERSION}

# 3. Login and push
docker login
docker push yourusername/telcoin-observer:latest
docker push yourusername/telcoin-observer:v${VERSION}

# 4. Verify
docker pull yourusername/telcoin-observer:latest
```

---

## Updating Documentation

After publishing, update `README.md` to include pull instructions:

```markdown
## Quick Start

Pull the pre-built image:
```bash
docker pull yourusername/telcoin-observer:latest
```

Or use docker-compose:
```bash
# Update docker-compose.yml to use:
image: yourusername/telcoin-observer:latest
```
```
