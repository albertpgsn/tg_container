#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.docker"
SETUP_VENV="${ROOT_DIR}/.setup-venv"
SKIP_DOCKER_INSTALL=0
NO_START=0

usage() {
  cat <<'EOF'
Usage: bash scripts/install_server.sh [options]

Options:
  --skip-docker-install  Do not install Docker when it is missing.
  --no-start             Prepare configuration and secrets without starting containers.
  -h, --help             Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-docker-install) SKIP_DOCKER_INSTALL=1 ;;
    --no-start) NO_START=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "${ROOT_DIR}"

for required in Dockerfile docker-compose.yml requirements.txt scripts/init_secrets.py; do
  if [[ ! -f "${required}" ]]; then
    echo "Missing ${required}. Run this installer from a complete repository checkout." >&2
    exit 1
  fi
done

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot detect the operating system: /etc/os-release is unavailable." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  debian|ubuntu) DOCKER_OS="${ID}" ;;
  *) echo "Supported systems: Debian and Ubuntu. Detected: ${ID:-unknown}." >&2; exit 1 ;;
esac

if [[ -z "${VERSION_CODENAME:-}" ]]; then
  echo "VERSION_CODENAME is missing in /etc/os-release." >&2
  exit 1
fi

if [[ ${EUID} -eq 0 ]]; then
  SUDO=()
  DEPLOY_USER="${SUDO_USER:-root}"
else
  command -v sudo >/dev/null 2>&1 || { echo "sudo is required for package installation." >&2; exit 1; }
  sudo -v
  SUDO=(sudo)
  DEPLOY_USER="${USER:-$(id -un)}"
fi

install_prerequisites() {
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl python3 python3-venv
}

install_docker() {
  local architecture key_tmp source_tmp package
  local conflicting_packages=()
  for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
    if dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null | grep -qx installed; then
      conflicting_packages+=("${package}")
    fi
  done
  if [[ ${#conflicting_packages[@]} -gt 0 ]]; then
    echo "Conflicting distribution packages detected: ${conflicting_packages[*]}"
    [[ -t 0 ]] || { echo "Interactive confirmation is required before removing conflicting packages." >&2; exit 1; }
    read -r -p "Remove these packages and install Docker CE? [y/N]: " answer
    if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
      echo "Docker installation cancelled." >&2
      exit 1
    fi
    "${SUDO[@]}" apt-get remove -y "${conflicting_packages[@]}"
  fi

  architecture="$(dpkg --print-architecture)"
  key_tmp="$(mktemp)"
  source_tmp="$(mktemp)"

  curl -fsSL "https://download.docker.com/linux/${DOCKER_OS}/gpg" -o "${key_tmp}"
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  "${SUDO[@]}" install -m 0644 "${key_tmp}" /etc/apt/keyrings/docker.asc

  cat >"${source_tmp}" <<EOF
Types: deb
URIs: https://download.docker.com/linux/${DOCKER_OS}
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: ${architecture}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  "${SUDO[@]}" install -m 0644 "${source_tmp}" /etc/apt/sources.list.d/docker.sources
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  "${SUDO[@]}" systemctl enable --now docker
  rm -f "${key_tmp}" "${source_tmp}"
}

install_prerequisites

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  if [[ ${SKIP_DOCKER_INSTALL} -eq 1 ]]; then
    echo "Docker Engine with the Compose plugin is required." >&2
    exit 1
  fi
  echo "Installing Docker Engine from the official Docker APT repository..."
  install_docker
fi

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif "${SUDO[@]}" docker info >/dev/null 2>&1; then
  DOCKER=("${SUDO[@]}" docker)
else
  echo "Docker is installed but the daemon is unavailable." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  [[ -t 0 ]] || { echo "Interactive terminal is required for first-time configuration." >&2; exit 1; }
  read -r -p "Telegram API ID: " TELEGRAM_API_ID
  if [[ ! "${TELEGRAM_API_ID}" =~ ^[0-9]+$ ]]; then
    echo "Telegram API ID must contain digits only." >&2
    exit 1
  fi
  read -r -p "Bootstrap admin username [admin]: " ADMIN_USERNAME
  ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
  if [[ ! "${ADMIN_USERNAME}" =~ ^[A-Za-z0-9_.-]{3,80}$ ]]; then
    echo "Admin username must be 3-80 characters: letters, digits, dot, underscore or hyphen." >&2
    exit 1
  fi
  umask 077
  printf 'TELEGRAM_API_ID=%s\nADMIN_BOOTSTRAP_USERNAME=%s\n' "${TELEGRAM_API_ID}" "${ADMIN_USERNAME}" >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created ${ENV_FILE}."
fi

if ! grep -Eq '^TELEGRAM_API_ID=[0-9]+$' "${ENV_FILE}"; then
  echo "${ENV_FILE} must contain a numeric TELEGRAM_API_ID." >&2
  exit 1
fi

required_secrets=(postgres_password database_url telegram_api_hash session_encryption_key admin_password_hash)
existing_secrets=0
for secret_name in "${required_secrets[@]}"; do
  [[ -f "${ROOT_DIR}/secrets/${secret_name}" ]] && existing_secrets=$((existing_secrets + 1))
done

if [[ ${existing_secrets} -eq 0 ]]; then
  [[ -t 0 ]] || { echo "Interactive terminal is required to create secrets." >&2; exit 1; }
  python3 -m venv "${SETUP_VENV}"
  "${SETUP_VENV}/bin/pip" install --disable-pip-version-check 'argon2-cffi>=23.1,<26.0' 'cryptography>=43,<46'
  "${SETUP_VENV}/bin/python" scripts/init_secrets.py
elif [[ ${existing_secrets} -ne ${#required_secrets[@]} ]]; then
  echo "The secrets directory is incomplete. Restore the missing secrets; the installer will not overwrite them." >&2
  exit 1
else
  echo "Existing secrets detected; keeping them unchanged."
fi

chmod 700 "${ROOT_DIR}/secrets"
chmod 600 "${ROOT_DIR}/secrets/"* "${ENV_FILE}"

if [[ ${NO_START} -eq 1 ]]; then
  echo "Configuration is ready. Containers were not started (--no-start)."
  exit 0
fi

echo "Building and starting Telegram CRM..."
"${DOCKER[@]}" compose --env-file "${ENV_FILE}" build --pull
"${DOCKER[@]}" compose --env-file "${ENV_FILE}" up -d

healthy=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done

"${DOCKER[@]}" compose --env-file "${ENV_FILE}" ps
if [[ ${healthy} -ne 1 ]]; then
  echo "Containers started, but the health check did not pass within 120 seconds." >&2
  echo "Inspect logs with: docker compose --env-file .env.docker logs --tail=200 app gateway db" >&2
  exit 1
fi

cat <<EOF

Telegram CRM is healthy and bound to server loopback only.

From your workstation, open an SSH tunnel:
  ssh -L 8080:127.0.0.1:8080 ${DEPLOY_USER}@SERVER_IP

Then open:
  http://127.0.0.1:8080

The installer did not modify SSH or firewall settings. Complete the hardening checklist in SECURITY.md.
EOF
