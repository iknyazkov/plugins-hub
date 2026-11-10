#!/bin/sh
set -u

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
CACHE_DIR="${HOME}/Library/Caches/yag"
CONFIG_DIR="${HOME}/.config/yag"
BINARY="${CACHE_DIR}/yag"
LOG_FILE="${CONFIG_DIR}/daemon.log"

mkdir -p "${CACHE_DIR}" "${CONFIG_DIR}"

if command -v go >/dev/null 2>&1; then
	if ! (cd "${PLUGIN_ROOT}/daemon" && go build -o "${BINARY}" .); then
		echo "yag: build failed, trying cached binary" >&2
		[ -x "${BINARY}" ] || exit 0
	fi
elif [ ! -x "${BINARY}" ]; then
	echo "yag: go toolchain not found and no cached binary at ${BINARY}" >&2
	exit 0
fi


nohup "${BINARY}" >>"${LOG_FILE}" 2>&1 &
