# install a ronin puppet override file or disables them

# the override file lives at /etc/puppet/ronin_settings

# if enabling, copy local override file in local dir to the remote host
# if disabling, move the override file to FILE.disabled.DATETIME

# override file format:

# heredoc
example_doc = """
# if you place this file at `/etc/puppet/ronin_settings`
# the `run-puppet.sh` script will use the values here.

# puppet overrides
PUPPET_REPO='https://github.com/aerickson/ronin_puppet.git'
PUPPET_BRANCH='moonshot_linux_py311_and_tc_update_plus_1804_hg_upgrade'
PUPPET_MAIL='aerickson@gmail.com'

# taskcluster overrides
# WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-1804-staging'
""".lstrip()