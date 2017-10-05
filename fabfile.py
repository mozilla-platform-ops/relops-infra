from fabric.api import run, env, sudo

env.hosts = ['rejh1.srv.releng.mdc1.mozilla.com','rejh2.srv.releng.mdc1.mozilla.com','rejh1.srv.releng.scl3.mozilla.com','rejh2.srv.releng.scl3.mozilla.com']

def run_puppet():
    sudo('puppet agent -t')
