#!/usr/bin/env bash

set -e
# set -x

run_remote_script() {
  local host="$1"
  local script="$2"
  if [[ -z "$host" || -z "$script" ]]; then
    echo "Usage: run_remote_script <host> <script.sh>"
    return 1
  fi

  # Use mktemp remotely to get a temp file path
  ssh "$host" 'tmp=$(mktemp /tmp/remote_script.XXXXXX.sh); echo $tmp' | {
    read -r remote_tmp
    echo "Uploading and running on $host:$remote_tmp"
    # Send the script via stdin and execute remotely
    cat "$script" | ssh "$host" "cat > '$remote_tmp' && chmod +x '$remote_tmp' && bash '$remote_tmp'; rm -f '$remote_tmp'"
  }
}

# Define the script to run remotely
REMOTE_SCRIPT=$(cat << 'EOF'
#!/usr/bin/env bash
set -e
echo "This is running on the remote host: $(hostname)"
# add more commands as needed
EOF
)

# Main
host="$1"
if [[ -z "$host" ]]; then
  echo "Usage: $0 <remote_host>"
  exit 1
fi
run_remote_script "$host" <(echo "$REMOTE_SCRIPT")