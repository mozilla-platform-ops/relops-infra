from fabric.api import env, sudo

# Prefer to handle myself the exceptions, to prevent the following behavior with puppet agent return code:
# puppet agent will use the following exit codes:
#    0: The run succeeded with no changes or failures; the system was already in the desired state.
#    1: The run failed, or wasn't attempted due to another run already in progress.
#    2: The run succeeded, and some resources were changed.
#    4: The run succeeded, and some resources failed.
#    6: The run succeeded, and included both changes and failures.

env.warn_only = True # ignore exceptions and handle them yurself

env.hosts = ['rejh1.srv.releng.mdc1.mozilla.com', 'rejh2.srv.releng.mdc1.mozilla.com', 'rejh1.srv.releng.scl3.mozilla.com', 'rejh2.srv.releng.scl3.mozilla.com']

command = 'puppet agent -t'

def run_puppet():
    execution = sudo(command)
    if (execution.return_code == 0) or (execution.return_code == 2):
        print execution.return_code
        execution.failed = False
    else:
        print "The command %s failed with error %s" %(command, execution.return_code)
        execution.failed = True
