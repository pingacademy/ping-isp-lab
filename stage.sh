#!/usr/bin/env bash
# Load a course stage into the lab and (re)deploy it.
#   ./stage.sh                 list stages
#   ./stage.sh 06-addressing   load that stage and redeploy
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -eq 0 ]; then
  echo "Available stages:"; ls configs/stages
  exit 0
fi

STAGE="configs/stages/$1"
[ -d "$STAGE" ] || { echo "No stage '$1'. Run ./stage.sh to list stages."; exit 1; }

cp "$STAGE"/*.conf configs/active/
sudo containerlab deploy -t ping-isp.clab.yml --reconfigure
echo "Loaded stage $1."
