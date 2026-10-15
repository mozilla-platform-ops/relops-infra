#!/usr/bin/env bash
# Temporary fresh-image path for a private ronin_puppet branch on Talos 24.04.
set -euo pipefail

moonshot_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
ronin_puppet_dir=${RONIN_PUPPET_DIR:-"$HOME/git/ronin_puppet"}

print_warning_banner() {
    local line row=0
    local -a colors=('255;170;48' '255;145;42' '255;120;39' '250;94;43' '238;69;49')
    local use_color=0
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != dumb ]; then
        use_color=1
    fi

    while IFS= read -r line; do
        if [ "$use_color" -eq 1 ]; then
            printf '\033[38;2;%sm%s\033[0m\n' "${colors[$row]}" "$line"
        else
            printf '%s\n' "$line"
        fi
        ((row += 1))
    done <<'BANNER'
__        ___    ____  _   _ ___ _   _  ____
\ \      / / \  |  _ \| \ | |_ _| \ | |/ ___|
 \ \ /\ / / _ \ | |_) |  \| || ||  \| | |  _
  \ V  V / ___ \|  _ <| |\  || || |\  | |_| |
   \_/\_/_/   \_\_| \_\_| \_|___|_| \_|\____|
BANNER
    printf '\nTEMPORARY PRIVATE-BRANCH ONESHOT - NOT THE NORMAL IMAGING PATH\n\n'
}

print_warning_banner

usage() {
    printf 'Usage: PUPPET_REPO=... PUPPET_BRANCH=... VAULT_SOURCE=... %s <talos-fqdn> [--confirm]\n' "$0" >&2
    printf 'Optional: WORKER_TYPE_OVERRIDE=<canary-worker-type>\n' >&2
    printf 'Set SKIP_REIMAGE=1 to resume setup on an already imaged host.\n' >&2
    exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
[ "$#" -eq 1 ] || [ "$2" = '--confirm' ] || usage
host=$1
case "$host" in
    t-linux64-ms-*.test.releng.mdc[12].mozilla.com) ;;
    *) usage ;;
esac
case "${host#t-linux64-ms-}" in
    [0-9][0-9][0-9].test.releng.mdc[12].mozilla.com) ;;
    *) usage ;;
esac

: "${PUPPET_REPO:?Set PUPPET_REPO to the private repository URL.}"
: "${PUPPET_BRANCH:?Set PUPPET_BRANCH to the test branch.}"
: "${VAULT_SOURCE:?Set VAULT_SOURCE to the local Vault YAML file.}"
case "$PUPPET_REPO" in
    https://github.com/aerickson/ronin_puppet-private.git | \
    https://github.com/mozilla-platform-ops/ronin_puppet-private.git | \
    https://github.com/mozilla-platform-ops/ronin_puppet.git) ;;
    *) printf 'Repository is not in the bootstrap allowlist.\n' >&2; exit 1 ;;
esac
case "$PUPPET_BRANCH" in
    '' | *[^A-Za-z0-9._/-]* | *'..'* | */ | /*)
        printf 'Invalid Puppet branch name.\n' >&2
        exit 1
        ;;
esac
case "${WORKER_TYPE_OVERRIDE:-}" in
    *[^A-Za-z0-9_-]*)
        printf 'Invalid worker type override.\n' >&2
        exit 1
        ;;
esac
case "${SKIP_REIMAGE:-0}" in
    0 | 1) ;;
    *) printf 'SKIP_REIMAGE must be 0 or 1.\n' >&2; exit 1 ;;
esac
[ -f "$VAULT_SOURCE" ] && [ ! -L "$VAULT_SOURCE" ] || {
    printf 'Vault source must be a regular, non-symlink file.\n' >&2
    exit 1
}
[ -f "$moonshot_dir/oneshot_2404_x11_talos.sh" ] || {
    printf 'Missing moonshot oneshot in %s\n' "$moonshot_dir" >&2
    exit 1
}
[ -f "$ronin_puppet_dir/provisioners/linux/bootstrap_linux.sh" ] || {
    printf 'Missing provisioners/linux/bootstrap_linux.sh in %s\n' "$ronin_puppet_dir" >&2
    exit 1
}
[ -f "$ronin_puppet_dir/modules/linux_packages/files/yq-er.sh" ] || {
    printf 'Missing yq-er.sh in %s\n' "$ronin_puppet_dir" >&2
    exit 1
}

[ -f "$ronin_puppet_dir/modules/puppet/files/github_known_hosts" ] || {
    printf 'Missing pinned GitHub host keys.\n' >&2
    exit 1
}
if [ "$PUPPET_BRANCH" = master ] && [ "$PUPPET_REPO" != 'https://github.com/mozilla-platform-ops/ronin_puppet.git' ]; then
    printf 'Standard bootstrap uses an unauthenticated archive for master; select a private branch name.\n' >&2
    exit 1
fi

printf 'Host: %s\nRole: gecko_t_linux_2404_talos\nRepository: %s\nBranch: %s\n' \
    "$host" "$PUPPET_REPO" "$PUPPET_BRANCH"
printf 'Worker type override: %s\n' "${WORKER_TYPE_OVERRIDE:-<none>}"
if [ -n "${WORKER_TYPE_OVERRIDE:-}" ]; then
    printf 'ronin_settings: generated with repo, branch, and worker type override\n'
else
    printf 'ronin_settings: generated with repo and branch\n'
fi
if [ "${SKIP_REIMAGE:-0}" = 1 ]; then
    printf 'Reimage: skipped (retry mode)\n'
else
    printf 'Reimage: enabled\n'
fi
printf 'First converge: standard bootstrap_linux.sh from this checkout (automatic reboot)\n'

if [ "${2:-}" != '--confirm' ]; then
    printf '\nDry run only. Add --confirm to execute these steps.\n'
    exit 0
fi

ssh_target="relops@${host}"
printf '\nRunning moonshot imaging/SSH phase...\n'
(
    cd "$moonshot_dir"
    SKIP_CONVERGE=1 ./oneshot_2404_x11_talos.sh "$host" --confirm
)

ssh -o BatchMode=yes "$ssh_target" 'sudo -n true'
# Do not overwrite a running bootstrap's files or settings on a retry.
ssh "$ssh_target" 'sudo -n bash -s' <<'REMOTE_PREFLIGHT'
set -euo pipefail
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ]
case "$(systemctl show ronin-bootstrap.service -p ActiveState --value)" in
    active|activating|deactivating) echo 'Bootstrap is already running; refusing overlap.' >&2; exit 1 ;;
esac
[ ! -e /etc/puppet/last_run_metadata.json ] || {
    echo 'Host has already converged; use normal run-puppet instead of bootstrap.' >&2; exit 1;
}
if [ -e /etc/puppet_role ]; then
    [ "$(cat /etc/puppet_role)" = gecko_t_linux_2404_talos ]
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git wget python3-yaml
install -d -o root -g root -m 0700 /root/ronin-bootstrap /root/.ssh
REMOTE_PREFLIGHT

printf '\nStaging Vault, pinned GitHub host keys, and standard bootstrap...\n'
vault_install_script=$(cat <<'INSTALL_VAULT'
set -eu
umask 077
vault_tmp=$(mktemp /root/.vault.yaml.XXXXXX)
trap 'rm -f -- "$vault_tmp"' EXIT HUP INT TERM
cat > "$vault_tmp"
chown root:root "$vault_tmp"
chmod 0640 "$vault_tmp"
mv -f -- "$vault_tmp" /root/vault.yaml
INSTALL_VAULT
)
printf -v quoted_vault_install '%q' "$vault_install_script"
# shellcheck disable=SC2029 # Fixed source is quoted as one shell argument.
ssh "$ssh_target" "sudo -n /bin/bash -c $quoted_vault_install" < "$VAULT_SOURCE"
stream_root_file() {
    local source=$1 destination=$2 mode=$3
    # All destinations and modes below are fixed, not operator input.
    # shellcheck disable=SC2029
    ssh "$ssh_target" "sudo -n /bin/sh -c 'set -eu; umask 077; cat > $destination; chmod $mode $destination'" < "$source"
}
stream_root_file "$ronin_puppet_dir/modules/puppet/files/github_known_hosts" /root/.ssh/known_hosts 0644
stream_root_file "$ronin_puppet_dir/provisioners/linux/bootstrap_linux.sh" /root/ronin-bootstrap/bootstrap_linux.sh 0700
stream_root_file "$ronin_puppet_dir/modules/linux_packages/files/yq-er.sh" /usr/local/bin/yq-er 0755

# Quote operator-supplied values as shell words; the remote source is fixed.
remote_command='sudo -n env'
for value in "PUPPET_REPO=$PUPPET_REPO" "PUPPET_BRANCH=$PUPPET_BRANCH" \
    "WORKER_TYPE_OVERRIDE=${WORKER_TYPE_OVERRIDE:-}"; do
    printf -v quoted_value '%q' "$value"
    remote_command+=" $quoted_value"
done
remote_command+=' /bin/bash -s'
# shellcheck disable=SC2029 # Values are explicitly shell-quoted above.
ssh "$ssh_target" "$remote_command" <<'REMOTE_STAGE'
set -euo pipefail
umask 077
install -d -o root -g root -m 0755 /etc/puppet
printf '%s\n' gecko_t_linux_2404_talos > /etc/puppet_role
chmod 0644 /etc/puppet_role
python3 - <<'INSTALL_KEY'
import os
from pathlib import Path
import tempfile
import yaml
slug = os.environ['PUPPET_REPO'][len('https://github.com/'):-len('.git')]
try:
    vault = yaml.safe_load(Path('/root/vault.yaml').read_text())
    key = vault['github']['deploy_keys'][slug]['private_key']
    if not isinstance(key, str) or not key.strip():
        raise ValueError()
except Exception:
    raise SystemExit('Vault could not be parsed or the repository deploy key is missing.')
keyname = slug.replace('/', '_').replace('-', '_') + '_deploy_key'
fd, temporary = tempfile.mkstemp(prefix='.deploy-key.', dir='/root/.ssh')
try:
    with os.fdopen(fd, 'w') as output:
        output.write(key.rstrip('\n') + '\n')
    os.replace(temporary, Path('/root/.ssh') / keyname)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('Repository deploy key installed; value not displayed.')
INSTALL_KEY
settings_tmp=$(mktemp /etc/puppet/.ronin_settings.XXXXXX)
trap 'rm -f -- "$settings_tmp"' EXIT
{
    printf 'PUPPET_REPO=%q\n' "$PUPPET_REPO"
    printf 'PUPPET_BRANCH=%q\n' "$PUPPET_BRANCH"
    printf '%s\n' "PUPPET_MAIL='aerickson@mozilla.com'"
    if [ -n "$WORKER_TYPE_OVERRIDE" ]; then
        printf 'WORKER_TYPE_OVERRIDE=%q\n' "$WORKER_TYPE_OVERRIDE"
    fi
} > "$settings_tmp"
chmod 0600 "$settings_tmp"
mv -f -- "$settings_tmp" /etc/puppet/ronin_settings
trap - EXIT
sha256sum /root/ronin-bootstrap/bootstrap_linux.sh > /root/ronin-bootstrap/bootstrap.sha256
cat > /root/ronin-bootstrap/run.sh <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
exec 9>/run/lock/ronin-bootstrap.lock
flock -n 9 || { echo 'Another bootstrap is running.' >&2; exit 1; }
sha256sum -c /root/ronin-bootstrap/bootstrap.sha256
# shellcheck source=/dev/null
source /etc/puppet/ronin_settings
export PUPPET_REPO PUPPET_BRANCH
slug=${PUPPET_REPO#https://github.com/}
slug=${slug%.git}
keyname=${slug//\//_}
keyname=${keyname//-/_}
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="url.ssh://git@ssh.github.com:443/${slug}.git.insteadOf"
export GIT_CONFIG_VALUE_0="$PUPPET_REPO"
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -F /dev/null -i /root/.ssh/${keyname}_deploy_key -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts -o GlobalKnownHostsFile=/dev/null"
revision=$(git ls-remote --exit-code "$PUPPET_REPO" "refs/heads/$PUPPET_BRANCH")
[ -n "$revision" ] || { echo 'Branch does not exist or deploy-key access failed.' >&2; exit 1; }
printf 'Bootstrap branch reference: %s\n' "$revision"
umask 022
logfile="/root/ronin-bootstrap/bootstrap-$(date -u +%Y%m%dT%H%M%SZ).log"
printf 'Starting standard bootstrap; log: %s\n' "$logfile"
exec /bin/bash /root/ronin-bootstrap/bootstrap_linux.sh -l "$logfile"
LAUNCHER
chmod 0700 /root/ronin-bootstrap/run.sh
systemctl reset-failed ronin-bootstrap.service 2>/dev/null || true
systemd-run --unit=ronin-bootstrap /bin/bash /root/ronin-bootstrap/run.sh
REMOTE_STAGE

printf '\nStandard bootstrap started independently of SSH. It reboots automatically after convergence.\n'
printf 'Progress: ssh %s %q\n' "$ssh_target" 'sudo journalctl -u ronin-bootstrap.service --no-pager -n 20'
printf 'Bootstrap logs: /root/ronin-bootstrap/bootstrap-*.log\n'
printf 'After reboot, connect using your normal account and run: sudo /usr/local/bin/generic-worker-health-check\n'
