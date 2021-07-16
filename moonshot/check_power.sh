#!/bin/bash

command="set node power on all"
command="show cartridge power all"
echo "command to send: ${command}"

names=${@:-""}
echo $names

echo -n "ILO admin password:"; read -s password; echo

for H in $names; do
    name=${H##*@}
    echo $name
    ping -c1 -q -w5 "$name" &>/dev/null && \
    {
    echo logging into $name
            echo -e "${password}\n" | ./moon_ilo_command.exp --hostname "$H" --command "${command}"
    }
done
