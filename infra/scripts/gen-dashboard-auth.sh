#!/usr/bin/env bash
# Gera hash bcrypt para o basic auth do dashboard do Traefik.
# Uso: ./gen-dashboard-auth.sh admin 'senha-forte'
# Requer apache2-utils: apt-get install -y apache2-utils
# Cole o resultado em infra/traefik/dynamic.yml (no arquivo, use $ simples).
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Uso: $0 <usuario> <senha>" >&2
  exit 1
fi

if ! command -v htpasswd >/dev/null 2>&1; then
  echo "htpasswd não encontrado. Instale com: apt-get install -y apache2-utils" >&2
  exit 1
fi

htpasswd -nbB "$1" "$2"
