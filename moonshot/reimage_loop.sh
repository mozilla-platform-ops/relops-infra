echo -n "ILO admin password:"; read -s password; echo
echo -n "kickstart password:"; read -s kickstart; echo
for c in $@; do echo $c; echo -e "$password\n$kickstart\n" | ./reimage_watch.exp $(./translate_ms_name.sh $c 2>&1 | grep "\-\-hostname"); done | tee reimage.$(date +"%H:%M:%S").log
