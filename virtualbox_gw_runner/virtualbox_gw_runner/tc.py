import os
import shutil
import subprocess
import sys
import tempfile
import time

import jinja2


# idea: run w-r/s-w long enough to generate a config then kill it
#   so we can use the config inside the guest
#
#   start-worker process (a guess)
#    - query metadata
#    - attempt to register worker
#    - generate config
#    - run g-w
#
def run_wr_locally_until_config_generated(noop=False, tc_component_dir=""):
    # we just want the name, we don't want it to exist yet...
    # temp_gw_config_file = tempfile.NamedTemporaryFile(dir="/tmp", prefix="gw_config", delete=False).name
    with tempfile.NamedTemporaryFile(
        dir="/tmp", prefix="gw_config.", delete=True
    ) as tmpfile:
        temp_gw_config_file = tmpfile
    temp_wr_config_file = tempfile.NamedTemporaryFile(
        dir="/tmp", prefix="wr_config.", delete=False
    )

    print(f"  temp wr file: {temp_wr_config_file.name}")
    print(f"    temp gw file: {temp_gw_config_file.name}")

    config = f"""
provider:
    providerType: google
worker:
    implementation: generic-worker
    path: {tc_component_dir}/generic-worker-simple
    configPath: {temp_gw_config_file.name}
    """
    with open(temp_wr_config_file.name, "w") as f:
        f.write(config)

    print("  running w-r until g-w config exists...")

    command = f"{tc_component_dir}/start-worker {temp_wr_config_file.name}"
    print(f"  command is: '{command}'")
    proc = subprocess.Popen(
        f"{tc_component_dir}/start-worker {temp_wr_config_file.name}",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    while True:
        # TODO: pull out into constant.
        if not proc.poll():
            print("process has exited... not good...")
            stdout, stderr = proc.communicate()
            print(stdout.decode("UTF-8"))
            print(stderr.decode("UTF-8"))
        if os.path.isfile(temp_gw_config_file.name):
            print("  found g-w config file, killing w-r...")
            proc.terminate()
            break
        time.sleep(1)

    return temp_gw_config_file.name


# inject things into config
# - used when running wr in the vbox vm
def generate_wr_config(wr_template_file=""):
    # was used for workerId
    # hostname = socket.gethostname()
    # "workerId": "gecko-t-t-linux-vm-2204-wayland-inggpsqgqlkkrbxh4jmtsg"
    # trimmed_hostname = hostname.split("-")[-1]
    # now use gcp id for workerId
    gcp_id_cmd = "curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/id'"
    result = subprocess.run(gcp_id_cmd, shell=True, stdout=subprocess.PIPE)
    gcp_id = result.stdout.decode("utf-8").strip()

    external_ip_cmd = "curl -s -H 'Metadata-Flavor: Google' http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"
    result = subprocess.run(external_ip_cmd, shell=True, stdout=subprocess.PIPE)
    external_ip = result.stdout.decode("utf-8").strip()

    zone_cmd = 'curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone'
    result = subprocess.run(zone_cmd, shell=True, stdout=subprocess.PIPE)
    zone = result.stdout.decode("utf-8")
    # output: "projects/559515877712/zones/us-central1-a"
    #  we first go to `['projects', '559515877712', 'zones', 'us-central1-a']`
    #  then ['us', 'central1'] before joining with '-'
    region = "-".join(zone.split("/")[-1].split("-")[: 1 - 2])

    access_token = os.environ.get("TC_ACCESS_TOKEN")
    client_id = os.environ.get("TC_CLIENT_ID")

    if not client_id:
        msg = "TC_CLIENT_ID is not set, but must be!"
        print(msg)
        sys.exit(1)
        # raise Exception(msg)

    if not access_token:
        msg = "TC_ACCESS_TOKEN is not set, but must be!"
        print(msg)
        sys.exit(1)
        # raise Exception(msg)

    print(f"  worker id: {gcp_id}")
    print(f"  external ip: {external_ip}")
    print(f"  region: {region}")
    print(f"  client id: {client_id}")
    print("  access token: REDACTED")

    # sed
    # run_command(
    #     f"sed -u 's/\$HOSTNAME/{hostname}/g;s/\$IP_ADDRESS/{external_ip}/g' worker-runner-config.template > worker-runner-config",
    #     verbose=False,
    # )

    # jinja2
    templateLoader = jinja2.FileSystemLoader(searchpath="./")
    templateEnv = jinja2.Environment(loader=templateLoader)
    template = templateEnv.get_template(wr_template_file)
    output = template.render(
        worker_id=gcp_id,
        external_ip=external_ip,
        region=region,
        client_id=client_id,
        access_token=access_token,
    )
    with open(wr_template_file, "w") as f:
        f.write(output)


# def run_wr_in_guest(noop=False):
#     if noop:
#         noop_time = 20
#         print(f"  run_wr_in_guest: noop mode, sleeping {noop_time}s...")
#         time.sleep(noop_time)
#         return
#     ssh = get_connection()

#     # wait for host to be ready
#     while True:
#         try:
#             ssh.exec_command("ls")
#             break
#         except:
#             time.sleep(1)

#     # worker-runner's binary is start-worker
#     command = f"{TC_DEPLOY_DIR}/start-worker {WR_CONFIG_PATH} 2>&1"
#     _stdin, stdout, _stderr = ssh.exec_command(command)

#     # async output
#     while True:
#         line = stdout.readline()
#         if not line:
#             break
#         print(line, end="")

#     # synchronous output
#     # print(stdout.read().decode())

#     print(f"  exit code: {stdout.channel.recv_exit_status()}")
#     if stdout.channel.recv_exit_status() != 0:
#         raise VBGWRNonZeroSSHException

#     ssh.close()


def copy_wr_config():
    shutil.copyfile("worker-runner-config.template", "worker-runner-config")
    return
