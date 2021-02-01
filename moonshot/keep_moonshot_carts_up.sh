#!/bin/bash

run_time_max="120 minutes ago"
idle_time_max="30 minutes ago"

provisioner='releng-hardware'
declare -A queued_tasks
export provisioner
export queued_tasks

declare -A carts
export carts
declare -A hosts
export hosts
declare -A hostnames
export hostnames


hostname_prefixes=${1:-t-linux64-ms- t-w1064-ms-}
#hostname_prefixes=${1:-t-linux64-ms-}
#hostname_prefixes=${1:-t-w1064-ms-}
skip_hosts_file="skip_hosts.txt"

# seconds between checks: days=60*60*24
repeat_time=${2:-1800}

root_url="https://firefox-ci-tc.services.mozilla.com/api/queue"
export root_url

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

ilo_user=${3:-Administrator}
echo -n "ILO admin password:"; read -s password; echo
telegraf_user=relops_wo
echo -n "telegraf ${telegraf_user} password:"; read -s telegraf_password; echo


# we could ignore know dev/staging:
# staging linux: t-[^-]*-ms-\(240\|280\|394\|395\)

# alternative, get full hostname lists for vlans:
# use_nmap_ips=false
# if $use_nmap_ips; then
#   wintest_vlan=$(nmap -R -sn -Pn 10.49.58.6/24 -oG - | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com.*Down$' | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com')
#   # Host: 10.49.58.101 (t-linux64-xe-299.test.releng.mdc1.mozilla.com)      Status: Down
#   test_vlan=$(nmap -R -sn -Pn 10.49.40.1/24 -oG - | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com.*Down$' | grep -o 't-[^ ]*test\.releng.mdc..mozilla.com')
# fi

function get_hostname() {
  I=$1
  for prefix in $hostname_prefixes; do # t-linux64-ms- t-w1064-ms-; do
    if [[ $prefix == "t-w"* ]]; then
      vnet="win"
    else
      vnet=""
    fi
    for dc in 1 2; do
      fqdn="${prefix}${I}.${vnet}test.releng.mdc${dc}.mozilla.com"
      host ${fqdn} 2>&1 >/dev/null \
        && ( echo $fqdn; break 2 )
    done
  done
}
export -f get_hostname

function echo_no_ping() {
  I=$1
  fqdn=$2
  ping -q -c1 -w5 ${fqdn} 2>&1 >/dev/null \
    || echo ${I} ${fqdn}
}

function find_hosts() {
  for x in {0..13}; do
    for n in {1..45}; do
      i=$(( x * 45 + n ))
      I=$(printf %03d $i )
      C=$(( x + 1 ))
      host=$(get_hostname $I)
      if [ -z $host ]; then
        continue
      fi
      W=${host%%.*}
      dc=$(echo $host | cut -d\. -f4)
      tags=",cart=$n,chassis=$C,host=$host,id=$I,workerId=$W,group=$dc id=${I}i,"
      carts[$I]=$tags
      hosts[$host]=$tags
      hostnames[$i]=$host
      hostnames[$I]=$host
    done
    wait
  done
}
find_hosts

function find_no_ping() {
  mv ${skip_hosts_file}.log ${skip_hosts_file}.log.old >/dev/null 2>&1
  > ${skip_hosts_file}.log
  for I in {001..630}; do 
    host=${hostnames[$I]}
    if [ -z $host ]; then
      continue
    fi
    if grep "^${I}" "$skip_hosts_file" >/dev/null; then
      echo "$I" >> ${skip_hosts_file}.log
      continue
    fi
    echo_no_ping $I $host &
    jobs=$(jobs -p)
    if [ ${#jobs[*]} -ge 16 ]; then
      wait -n
    fi
  done
  wait
}
export -f find_no_ping

function current_hostlist() {
  for I in {001..630}; do 
    host=${hostnames[$I]}
    if [ -z $host ]; then
      continue
    fi
    if grep "^${I}\$" "$skip_hosts_file" >/dev/null; then
      continue
    fi
    echo $I $host
  done
}
export -f current_hostlist

function is_older_than() {
    timestamp="$(echo $1 | cut -d' ' -f4 | sed -e 's/T/ /')"
    task_timestamp=$(date -u --date="$timestamp" +"%s")
    compare_time=$(date -u --date="$2" +"%s")
    [[ $task_timestamp -lt $compare_time ]]
}
export -f is_older_than

function check_last_task() {
    fqdn=$1
    printf "${fqdn}"

    hostname=${fqdn%%.*}
    dc_name=$(echo $fqdn | sed -e "s/.*\(mdc1\|mdc2\).*/\1/")
    provisioner='releng-hardware'
    workerType='None'
    taskId=''

    while IFS=" " read -r workerId workerType other; do
        taskId=$(wget -q -O - ${root_url}/v1/provisioners/${provisioner}/worker-types/${workerType}/workers/${dc_name}/${workerId} | grep 'taskId' | tail -1 | cut -d'"' -f4)
        break
    done < workers/${hostname} 2>/dev/null
    if [[ ${#taskId} -lt 2 ]]; then
        try_first="gecko-t-linux-talos gecko-t-win10-64-hw"
        worker_types="$try_first ${worker_types}"
        found_worker=false
        for workerType in $worker_types; do
            for workerId in ${hostname,,} ${hostname^^}; do
                taskId=$(wget -q -O - ${root_url}/v1/provisioners/${provisioner}/worker-types/${workerType}/workers/${dc_name}/${workerId} | grep 'taskId' | tail -1 | cut -d'"' -f4)
                if [[ ${#taskId} -gt 0 ]]; then
                    found_worker=true
                    printf " \"%s\":" $taskId
                    echo "$workerId $workerType" > "workers/${hostname}"
                    break
                fi
            done
        done
        if ! $found_worker; then
            printf " not found ${hostname} "
            return 1
        fi
    fi

    last_task=$(
       wget -q -O - ${root_url}/v1/task/${taskId}/status \
            | jq -r '.status.runs | .[] | [.state,.started,.ended] | @tsv'
            #| tr '\n' ' ' | grep -o "{[^{]*workerId.*${hostname}[^}]*}" \
            #| sed -e 's/[\t ]\+/ /g' -e 's/.*state.: .\([^"]*\)",.*started": "\([0-9:\.TZ\-]\+\)",\?\( "resolved":\( \)"\([0-9:\.TZ\-]\+\)"\)\?.*/\1 \2\4\5/'
    )
    last_task_status=$(echo $last_task | cut -d' ' -f1)
    last_task_started=$(echo $last_task | cut -d' ' -f2)
    last_task_ended=$(echo $last_task | cut -d' ' -f3)
    tasks_found=$?

    if [[ $tasks_found -ne 0 ]]; then
        printf " no tasks"
        if [[ ${queued_tasks[${provisioner}_${workerType}]} -gt 0 ]]; then
            printf " [queue ${workerType}]: ${queued_tasks[${provisioner}_${workerType}]}]"
            printf " N\n"
            return 1
        fi
    else
        printf " ${last_task_status} ${last_task_started}->${last_task_ended}"
        if [[ ! -z $last_task_ended ]]; then
            if is_older_than "$last_task_ended" "${idle_time_max}"; then
                last_task_name=$(wget -q -O - ${root_url}/v1/task/${taskId} | grep -A6 metadata | grep -o "name.*" | cut -d'"' -f3)
                printf " [end>${idle_time_max%% *}]"
                printf " \"${last_task_name}\""
                printf " [queue ${workerType}]: ${queued_tasks[${provisioner}_${workerType}]}]"
                if [[ ${queued_tasks[${provisioner}_${workerType}]} -gt 0 ]]; then
                    printf " Q\n"
                    return 1
                fi
                if [[ "$last_task_status" == "exception" ]]; then
                    printf " X\n"
                    return 1
                fi
            fi
        elif is_older_than "$last_task_started" "${run_time_max}"; then
            last_task_name=$(wget -q -O - ${root_url}/v1/task/${taskId} | grep -A6 metadata | grep -o "name.*" | cut -d'"' -f3)
            printf " [start>${run_time_max%% *}]"
            printf " \"${last_task_name}\""
            printf " S\n"
            return 1
        else
            printf " ${last_task_status} ${last_task_started}->${last_task_ended}"
        fi
    fi

    printf "\n"

    return 0
}
export -f check_last_task

function report_metric_each() {
  if [[ $1 == *"="* ]]; then
    tag=","${1}
    type=${2}
  else
    tag=""
    type=${1}
  fi
  shift
  shift
  ids=$@
  for id in ${ids[*]}; do 
    case $id in
      ''|*[!0-9]*) id=$(echo $id | grep -o '[0-9]\+$') ;;
      *) ;;
    esac
    tags=${carts[$id]}
    curl -s --user "$telegraf_user:$telegraf_password" -i -XPOST "https://telegraf.relops.mozops.net/write?db=relops" --data-binary "systemcheck,type=moonshot,event=${type}${tag}${tags}${type}=1" -o /dev/null -w "%{http_code}" >/dev/null
    #echo "systemcheck,type=moonshot,event=${type}${tag}${tags}${type}=1" -o /dev/null -w "%{http_code}" >> ./keep.metrics
  done
}
export -f report_metric_each 

function check_queues() {
    provisioner=$1
    workerType=$2
    last_task=$(
        wget -q -O - ${root_url}/v1/pending/${provisioner}/${workerType} \
            | grep pendingTasks | cut -d\: -f2
    )
    queued_tasks[${provisioner}_${workerType}]=$last_task
    echo ${provisioner}_${workerType} ${queued_tasks[${provisioner}_${workerType}]}
}
export -f check_queues

while true; do
  date
  NEXT_SECONDS=$((SECONDS + repeat_time))
  echo "${SECONDS} next:${NEXT_SECONDS}"
  (
    noping_file=nopinglist.log
    alllist=$(current_hostlist)
    nopinglist=$(find_no_ping | tee "$noping_file")
    echo no ping response [$(echo "${nopinglist}" | wc -w)]: ${nopinglist}
    ids=$( echo $nopinglist | grep -o '[0-9]\+ ' )
    echo no ping response [$(echo "${ids}" | wc -w)]: ${ids}
    report_metric_each noping ${ids} &
    
    worker_types=$(wget -q -O - ${root_url}/v1/provisioners/${provisioner}/worker-types/ | grep '\"workerType\"' | sed -e 's/.* "\([^"]*\)",$/\1/')
    export worker_types

    for worker_type in $worker_types; do
        check_queues $provisioner $worker_type
    done
    wait
    
    rm reboot_workers/* 2>/dev/null
    rm not_reboot_workers/* 2>/dev/null
    mkdir reboot_workers 2>/dev/null
    mkdir not_reboot_workers 2>/dev/null
    >checked_workers
    echo "${alllist}" \
      | xargs --max-procs=20 -I{} bash -c '\
      worker="{}"
      fqdn=$(echo $worker| cut -d\  -f2)
      if ! check_last_task $fqdn; then
          if ! grep "^${worker}$" "'$skip_hosts_file'" ; then
              if grep "^${worker}$" "'$noping_file'" ; then
                  touch reboot_workers/"${worker%% *}"
              else
                  echo "Ping was okay from ${worker}"
              fi
          else
              touch not_reboot_workers/"${worker%% *}"
          fi
      fi | tee -a checked_workers
      exit 0'

    not_found=$(grep -Eo "not found *[^ ]*" checked_workers | cut -d\  -f3)
    echo did not find [$( echo "${not_found}" | wc -w )]: $not_found
    ids=$( echo "${not_found}" | grep -o '[0-9]\+$' )
    report_metric_each missing ${ids} &

    missing=$(find reboot_workers -maxdepth 1 -type f -printf "%f\n")
    echo now will reboot [$( echo "${missing}" | wc -w )]: $missing
    report_metric_each hung $missing &

    not_missing=$(find not_reboot_workers -maxdepth 1 -type f -printf "%f\n")
    echo will not reboot [$( echo "${not_missing}" | wc -w )]: $not_missing

    if [[ ! -z ${missing} ]]; then
        echo "NOW rebooting ..."
        while read -r chassis nodes; do
            echo ": $chassis $nodes"
            echo -e "${password}\n" \
              | ./up_carts_on_chassis.exp --hostname ${ilo_user}@$chassis --nodes $nodes > reboot.${chassis}.$(date +"%H").log 2>&1 &
        done < <(./hostname_to_cart.sh $missing)
        wait
    fi 
  )
  
  function check_chassis() {
    chassis=$1
    carts=$(echo -e ${password}\n | ./check_power.sh Administrator@${chassis} \
      | grep -o 'c[0-9]\+n[0-9]\+\|Power State: \(On\|Off\)' \
      | tr $'\n' ';' \
      | sed -Ee 's/;Power State://g' -e 's/;(c)?/\n \1/g' \
      | grep Off \
      | cut -dn -f1 \
      | tr $'\n' ' ')
    printf "${carts}\n"
  }

  echo "now check if down..."
  for i in {1..14}; do
    for dc in {1..2}; do
      chassis=moon-chassis-${i}.inband.releng.mdc${dc}.mozilla.com
      host $chassis >/dev/null 2>&1 \
        && break
    done
    check_chassis $chassis \
      | xargs -I{} report_metric_each "chassis=$chassis" down {} &
    break
  done

  (
  nopinglist=$(find_no_ping)
  echo no ping response: ${nopinglist}
  ids=$( echo $nopinglist | grep -o '[0-9]\+ ' )
  report_metric_each noping ${ids} &
  ) &
  
  date
  i=0
  while [[ $SECONDS -lt $NEXT_SECONDS ]]; do
    printf "."
    sleep 30
    i=$(( i + 30 ))
  done
  
done
