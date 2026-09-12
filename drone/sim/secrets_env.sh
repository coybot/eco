# Sourced by the sim launchers. Fills in the account-specific endpoints from
# ~/.config/coybot/secrets.env so they stay out of the repository.
#
# Only fills variables the caller has not already set, so
# `IOT_ENDPOINT=... bash launch_fleet.sh` still wins over the file.
# shellcheck shell=bash

if [ -n "${COYBOT_SECRETS:-}" ]; then
  COYBOT_SECRETS="$COYBOT_SECRETS"
elif [ -f "$HOME/.config/coybot/secrets.env" ]; then
  COYBOT_SECRETS="$HOME/.config/coybot/secrets.env"
else
  # coybot-autonomy/eco writes the same keys; one file serves both checkouts
  COYBOT_SECRETS="$HOME/.config/coybot/secrets.env"
fi

if [ -f "$COYBOT_SECRETS" ]; then
  while IFS='=' read -r _k _v; do
    _k="${_k#export }"
    _k="${_k// /}"
    case "$_k" in '' | '#'*) continue ;; esac
    _v="${_v%\"}"; _v="${_v#\"}"
    _v="${_v%\'}"; _v="${_v#\'}"
    # Indirect expansion via eval rather than ${!_k}: this file gets sourced
    # from zsh too (interactive Mac shells), where ${!_k} means something else.
    if [ -z "$(eval "printf '%s' \"\${$_k-}\"")" ]; then
      export "$_k=$_v"
    fi
  done < "$COYBOT_SECRETS"
  unset _k _v
fi
