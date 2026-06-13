#!/usr/bin/env bash
# Instala Docker Engine + Compose v2 no Debian 12 (bookworm). Idempotente.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo ">> Instalando pré-requisitos..."
  apt-get update -y
  apt-get install -y --no-install-recommends ca-certificates curl gnupg

  echo ">> Adicionando chave GPG oficial da Docker (keyring dedicado)..."
  install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/debian/gpg \
      -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi

  echo ">> Adicionando repositório APT da Docker (arch + codename determinísticos)..."
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  echo ">> Instalando Engine + CLI + containerd + buildx + compose plugin..."
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
else
  echo ">> Docker já instalado: $(docker --version)"
fi

echo ">> Habilitando e iniciando o serviço..."
systemctl enable --now docker

echo ">> Verificação pós-instalação:"
docker --version
docker compose version        # plugin v2 (sem hífen)
docker buildx version

echo ">> OK: Docker e Compose v2 prontos."
