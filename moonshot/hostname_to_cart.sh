#!/usr/bin/env bash

shopt -s extglob
declare -i I

: ${1?"Usage: $0 hostname[s]/host_number[s]"}

declare -A chassis

for arg in $@; do
    I=${arg##+(0)}
    if [[ $I -gt 630 ]]; then
        c=$(( ( ( I - 1 ) - 30 ) / 45 + 2 ))
        n=$(( ( ( I - 1 ) - 630 ) % 45 + 1 ))
    elif [[ $I -gt 615 ]]; then
        c=$(( ( ( I - 1 ) - 15 ) / 45 + 1 - 13 ))
        n=$(( ( ( I - 1 ) - 615 ) % 45 + 1 + 30 ))
    elif [[ $I -gt 300 ]]; then
        c=$(( ( ( I - 1 ) + 15 ) / 45 + 1 ))
        n=$(( ( ( I - 1 ) + 15 ) % 45 + 1 ))
    else
        c=$(( ( I - 1 ) / 45 + 1 ))
        n=$(( ( I - 1 ) % 45 + 1 ))
    fi
    # echo $c $n
    chassis[$c]+="$n,"
done

for c in ${!chassis[@]}; do
    if [[ $c -gt 7 ]]; then
        dc="mdc2"
    else
        dc="mdc1"
    fi
    echo moon-chassis-${c}.inband.releng.${dc}.mozilla.com C${chassis[$c]%%,}N1
done
