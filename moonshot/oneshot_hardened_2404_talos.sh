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
    printf 'Set SKIP_REIMAGE=1 to retry setup without reimaging.\n' >&2
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
[ -f "$ronin_puppet_dir/bootstrap_hardened_puppet.sh" ] || {
    printf 'Missing bootstrap_hardened_puppet.sh in %s\n' "$ronin_puppet_dir" >&2
    exit 1
}
[ -f "$ronin_puppet_dir/modules/linux_packages/files/yq-er.sh" ] || {
    printf 'Missing yq-er.sh in %s\n' "$ronin_puppet_dir" >&2
    exit 1
}

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
printf 'First converge: deploy-key bootstrap from this checkout\n'

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
printf '\nInstalling first-converge prerequisites...\n'
ssh "$ssh_target" 'sudo -n bash -s' <<'REMOTE_SETUP'
set -euo pipefail
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || {
    printf 'Expected Ubuntu 24.04.\n' >&2
    exit 1
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3-yaml
install -d -m 0755 /etc/systemd/timesyncd.conf.d
printf '[Time]\nNTP=ntp.build.mozilla.org\n' > /etc/systemd/timesyncd.conf.d/mozilla.conf
systemctl restart systemd-timesyncd
if [ ! -x /opt/puppetlabs/bin/puppet ]; then
    package_dir=$(mktemp -d /root/openvox-bootstrap.XXXXXX)
    trap 'rm -rf -- "$package_dir"' EXIT
    curl --fail --location --retry 3 \
        'https://apt.voxpupuli.org/openvox8-release-ubuntu24.04.deb' \
        -o "$package_dir/openvox-release.deb"
    dpkg -i "$package_dir/openvox-release.deb"
    apt-get update
    apt-get -o Dpkg::Options::=--force-confnew remove -y puppet
    apt-get -o Dpkg::Options::=--force-confnew install -y openvox-agent
fi

install -d -m 0755 /usr/local/bin
REMOTE_SETUP

printf 'Installing yq-er and Puppet role...\n'
ssh "$ssh_target" 'sudo -n sh -c '\''umask 022; cat > /usr/local/bin/yq-er; chmod 0755 /usr/local/bin/yq-er'\''' \
    < "$ronin_puppet_dir/modules/linux_packages/files/yq-er.sh"
ssh "$ssh_target" 'sudo -n sh -c '\''set -eu; if [ -e /etc/puppet_role ]; then test "$(cat /etc/puppet_role)" = gecko_t_linux_2404_talos; else printf "%s\n" gecko_t_linux_2404_talos > /etc/puppet_role; fi'\'''

printf '\nRunning private-branch Puppet bootstrap...\n'
PUPPET_REPO="$PUPPET_REPO" \
PUPPET_BRANCH="$PUPPET_BRANCH" \
VAULT_SOURCE="$VAULT_SOURCE" \
WORKER_TYPE_OVERRIDE="${WORKER_TYPE_OVERRIDE:-}" \
"$ronin_puppet_dir/bootstrap_hardened_puppet.sh" "$ssh_target"

printf '\nFirst converge complete. Check normal run-puppet and reboot before pool admission.\n'
reboot_target="$(id -un)@${host}"
reboot_command='sudo -n systemctl reboot'
printf 'Reboot command: ssh %s %q\n' "$reboot_target" "$reboot_command"
if [ -t 0 ]; then
    if read -r -p 'Reboot host now? [y/N] ' reboot_answer; then
        case "$reboot_answer" in
            [Yy] | [Yy][Ee][Ss])
                ssh "$reboot_target" "$reboot_command"
                printf 'Reboot requested.\n'
                ;;
            *) printf 'Reboot deferred. Run the command above when ready.\n' ;;
        esac
    fi
fi
