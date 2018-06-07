#!/usr/bin/env bash

username="${0:-rollerdev}"
type="${1:-operator}"
range=${2:-14};
dc=${3:-2};

echo -n "ILO admin password:"; read -s password; echo
echo -n "user new password:"; read -s newpassword; echo

for I in $range; do
  echo -e "${password}\n${newpassword}\n" | \
    ./user_set.exp --hostname Administrator@moon-chassis-${I}.inband.releng.mdc${dc}.mozilla.com --user "$username" --type "$type"
done
echo "applied to moon-chassis-[$range] in mdc$dc"
