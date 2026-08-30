GIT_HASH ?= $(shell git log --format="%h" -n 1)

_BUILD_ARGS_BUILD_VERSION ?= $(shell yq .version config.yaml)
_BUILD_ARGS_BUILD_REVISION ?= ${GIT_HASH}
_BUILD_ARGS_BUILD_TIME ?= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
_CLOUDFLARE_SPEED_CLI_VERSION ?= 1.0.8

PLATFORMS ?= linux/arm64,linux/amd64
LOCAL_PLATFORM ?= linux/amd64
GITHUB_USERNAME ?= caraar12345
GITHUB_REPO ?= home-assistant-addons
ADDON_NAME ?= cloudflare-speedtest

build:
	docker buildx build --push \
		--platform $(PLATFORMS) \
		-t ghcr.io/${GITHUB_USERNAME}/${GITHUB_REPO}/${ADDON_NAME}:${_BUILD_ARGS_BUILD_REVISION} \
		-t ghcr.io/${GITHUB_USERNAME}/${GITHUB_REPO}/${ADDON_NAME}:${_BUILD_ARGS_BUILD_VERSION} \
		-t ghcr.io/${GITHUB_USERNAME}/${GITHUB_REPO}/${ADDON_NAME}:latest \
		--build-arg BUILD_TIME=${_BUILD_ARGS_BUILD_TIME} \
		--build-arg BUILD_REVISION=${_BUILD_ARGS_BUILD_REVISION} \
		--build-arg BUILD_VERSION=${_BUILD_ARGS_BUILD_VERSION} \
		--build-arg CLOUDFLARE_SPEED_CLI_VERSION=${_CLOUDFLARE_SPEED_CLI_VERSION} .

build_local:
	docker buildx build --load \
		--platform $(LOCAL_PLATFORM) \
		-t ghcr.io/${GITHUB_USERNAME}/${GITHUB_REPO}/${ADDON_NAME}:${_BUILD_ARGS_BUILD_REVISION} \
		--build-arg BUILD_TIME=${_BUILD_ARGS_BUILD_TIME} \
		--build-arg BUILD_REVISION=${_BUILD_ARGS_BUILD_REVISION} \
		--build-arg BUILD_VERSION=${_BUILD_ARGS_BUILD_VERSION} \
		--build-arg CLOUDFLARE_SPEED_CLI_VERSION=${_CLOUDFLARE_SPEED_CLI_VERSION} .
