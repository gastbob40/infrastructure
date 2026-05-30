#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y --no-install-recommends curl jq ca-certificates >/dev/null

fetch_players() {
  curl -fsS "https://frontend.cfx-services.net/api/servers/single/$1" | jq -r '.Data.clients'
}

update_channel() {
  channel_id=$1
  token=$2
  name=$3

  curl -fsS -X PATCH "https://discord.com/api/v10/channels/${channel_id}" \
    -H "Authorization: Bot ${token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg n "$name" '{name: $n}')" >/dev/null
}

FRATERNITY_PLAYERS=$(fetch_players "$FRATERNITY_SERVER_ID")
FRATWORLD_PLAYERS=$(fetch_players "$FRATWORLD_SERVER_ID")

echo "Fraternity: ${FRATERNITY_PLAYERS} | Fratworld: ${FRATWORLD_PLAYERS}"

update_channel "$FRATERNITY_CHANNEL_ID" "$FRATERNITY_BOT_TOKEN" "FA: ${FRATERNITY_PLAYERS} Joueurs IG 🎮"
update_channel "$FRATWORLD_CHANNEL_ID"  "$FRATWORLD_BOT_TOKEN"  "🔶️』${FRATWORLD_PLAYERS} JOUEURS IG 🎮"
