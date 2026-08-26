# Sourced by the sim launchers. Fills in the account-specific endpoints from
# ~/.config/astral/secrets.env so they stay out of the repository.
#
# Only fills variables the caller has not already set, so
# `IOT_ENDPOINT=... bash launch_fleet.sh` still wins over the file.
# shellcheck shell=bash

if [ -n "${ASTRAL_SECRETS:-}" ]; then
  PRESIDIO_SECRETS="$ASTRAL_SECRETS"
elif [ -f "$HOME/.config/astral/secrets.env" ]; then
  PRESIDIO_SECRETS="$HOME/.config/astral/secrets.env"
else
  # presidio-autonomy/eco writes the same keys; one file serves both checkouts
  PRESIDIO_SECRETS="$HOME/.config/presidio/secrets.env"
fi

if [ -f "$PRESIDIO_SECRETS" ]; then
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
  done < "$PRESIDIO_SECRETS"
  unset _k _v
fi
