#!/bin/bash

# for each moonshot cartridge
#   find it in dns
#   if ping fails
#     check last task
#     if last task exception or activity >30m ago,
#       ilo: 
#         if cartridge is powered on
#           power cycle
#         fi
#     fi
#   fi
# fi
#     

echo -n "ILO admin password:"; read -s password; echo

# we could ignore know dev/staging:
# staging linux: t-[^-]*-ms-\(240\|280\|394\|395\)

# alternative, get full hostname lists for vlans:
#wintest_vlan=$(nmap -R -sL -Pn 10.49.58.6/24 | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com')
#test_vlan=$(nmap -R -sL -Pn 10.49.40.1/24 | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com')

# 14 chassis with 45 cartridges in each
function find_no_ping() {
  for I in {001..630}; do 
    found=false
    for group in linux64 w1064; do
      if [[ $group == "w"* ]]; then
        vnet="win"
      else
        vnet=""
      fi
      for dc in 1 2; do
        (fqdn="t-${group}-ms-${I}.${vnet}test.releng.mdc${dc}.mozilla.com"
        host ${fqdn} 2>&1 >/dev/null \
          && (ping -q -c1 -w5 ${fqdn} 2>&1 >/dev/null \
            || ( echo ${I} ${fqdn}; break ) ) &
        )&
      done
    done
  done
  wait
}
export -f find_no_ping
newlist=$(find_no_ping)
echo "no ping response: ${newlist}"

function is_older_than() {
    timestamp="$(echo $1 | cut -d' ' -f4 | sed -e 's/T/ /')"
    task_timestamp=$(date -u --date="$timestamp" +"%s")
    compare_time=$(date -u --date="$2" +"%s")
    [[ $task_timestamp -lt $compare_time ]]
}
export -f is_older_than

worker_types=$(wget -q -O - https://queue.taskcluster.net/v1/provisioners/releng-hardware/worker-types/ | grep '\"workerType\"' | sed -e 's/.* "\([^"]*\)",$/\1/')
export worker_types

function check_last_task() {
    fqdn=$1
    printf "fqdn: $fqdn "

    false && (if ! $(ping -q -c 1 -W 10 "${fqdn}" >/dev/null 2>&1 ); then
        printf "no ping "
    else
        return 0
    fi
    )
    hostname=${fqdn%%.*}
    dc_name=$(echo $fqdn | sed -e "s/.*\(mdc1\|mdc2\).*/\1/")

    try_first="gecko-t-linux-talos gecko-t-win10-64-hw"
    worker_types="$try_first ${worker_types}"
    found_worker=false
    for workerType in $worker_types; do
        taskId=$(wget -q -O - https://queue.taskcluster.net/v1/provisioners/releng-hardware/worker-types/${workerType}/workers/${dc_name}/${hostname} | grep 'taskId' | tail -1 | cut -d'"' -f4)
        if [[ ${#taskId} -gt 0 ]]; then
            found_worker=true
            printf "\"%s\":" $taskId
            break
        fi
    done
    if ! $found_worker; then
        printf "not found "
        return 1
    fi
    #tc_url="https://tools.taskcluster.net/provisioners/releng-hardware/worker-types/${workerType}/workers/${dc_name}/${hostname}"

    last_task=$(
       wget -q -O - https://queue.taskcluster.net/v1/task/${taskId}/status \
            | tr '\n' ' ' | grep -o "{[^{]*workerId.*${hostname}[^}]*}" \
            | sed -e 's/[\t ]\+/ /g' -e 's/.*state.: .\([^"]*\)",.*started": "\([0-9:\.TZ\-]\+\)",\?\( "resolved":\( \)"\([0-9:\.TZ\-]\+\)"\)\?.*/\1 \2\4\5/'
    )
    last_task_status=$(echo $last_task | cut -d' ' -f1)
    last_task_started=$(echo $last_task | cut -d' ' -f2)
    last_task_ended=$(echo $last_task | cut -d' ' -f3)
    tasks_found=$?
    if [[ -z $last_task_ended ]]; then
        last_task_activity=$last_task_started
    else
        last_task_activity=$last_task_ended
    fi

    #last_task_name=",\""$(wget -q -O - https://queue.taskcluster.net/v1/task/${taskId} | grep -A6 metadata | grep -o "name.*" | cut -d'"' -f3)"\""

    old_tasks=false
    if [[ $tasks_found -ne 0 ]]; then
        printf "\"%s\"," "no tasks"
    else
        if is_older_than "$last_task_activity" "30 minutes ago"; then
            printf "[\"[>%s]\",\"%s\"%s]," "30m" "${last_task_status}" "${last_task_started}" "${last_task_ended}" "${last_task_name}"
            old_tasks=true
        else
            printf "[\"%s\"%s]," "${last_task}" "${last_task_name}"
        fi
    fi

    if [[ "$last_task_status" == "exception" ]] || $old_tasks; then
        printf "$last_task_status "
        return 1
    fi

    return 0
}
export -f check_last_task

>reboot_workers
echo "${newlist}" \
  | xargs --max-procs=1 -I{} bash -c '\
  worker="{}";\
  fqdn=$(echo $worker| cut -d\  -f2);\
  check_last_task $fqdn \
    || echo "${worker}" >> reboot_workers; \
  echo;\
  exit 0'
echo ""
missing=$(cat reboot_workers | cut -d\  -f1)
echo "now will reboot: $missing"
echo "${missing}" | xargs -P 4 -I {}  bash -c 'c={}; printf "$c"; echo -e "'$password'\n" | ./reboot_if_on.exp $(./translate_ms_name.sh $c 2>&1 | grep "\-\-hostname") | tee reboot.${c}.$(date +"%H:%M:%S").log; exit 0'
