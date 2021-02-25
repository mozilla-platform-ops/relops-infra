#!/bin/bash

work_dir=work_dir
remote_script_dir=c:\\programdata\\puppetlabs\\ronin\\audit
remote_restore_script=$remote_script_dir\\force_restore.ps1
iplist=$arg1

Help() {
  echo "Trigger restore for Windows Moonshot workers"
  echo 
  echo "SSH needs to be enabled and the force_restore.ps1 must be on the worker"
  echo "Must have the winaudit ssh key to work"
  echo
  echo "options:"
  echo 
  echo "-f | --file  Specify a file containing a list of node IP addresses to be restored"
  echo "-i | --ip    Specify a single node IP address"
  echo 
  echo "-h | --help  Print this Help."
  echo 
  echo "This is mainly part of the audit and recovery script"
  echo "It can be used independently but concider using the deployment script"

  exit
}

while [ $# -gt 0 ] ; do
  case $1 in
    -f | --file) file="$2" ;;
    -i | --ip) one_ip="$2" ;;
    -h | --help) Help ;;
  esac
  shift
done

function restore_remote() {

  if [ -z "$file" ] && [ -z "$one_ip" ];
  then 
    echo Must specify file or single ip address!
    exit 3
  elif [[ ! -z "$file" ]] && [[ ! -z "$one_ip" ]];
  then
    echo Must be either file or an ip address!
    exit 3 
  elif [[ ! -z "$file" ]] && [ -z "$one_ip" ];
  then
    echo this is my file $file
    readarray -t  restore < $file
    for ip in "${restore[@]}"; do 
      name=`dig +short -x $ip`
      echo Attempting a remote restore of $name at $ip
      ssh -o ConnectTimeout=5  -o StrictHostKeyChecking=no -i  $sshkey administrator@$ip powershell -executionpolicy bypass -file $remote_restore_script  2>/dev/null
    done
  elif [ -z "$file" ] && [[ ! -z "$one_ip" ]];
  then
    echo this is a fake ip $ip
    name=`dig +short -x $one_ip`
    echo Attempting a remote restore of $name at $one_ip
    ssh -o ConnectTimeout=5  -o StrictHostKeyChecking=no -i  $sshkey administrator@$one_ip powershell -executionpolicy bypass -file $remote_restore_script  2>/dev/null
  fi
}


restore_remote
