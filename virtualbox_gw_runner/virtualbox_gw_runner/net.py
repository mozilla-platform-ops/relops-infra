import socket
import time

import paramiko


def get_connection(
    ssh_host, ssh_user, ssh_port=22, banner_timeout=200, timeout=10, auth_timeout=10
):
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    # ssh.set_combine_stderr(True)
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY)
    # TODO: set a timeout for this outer while or something?
    while True:
        try:
            ssh.connect(
                ssh_host,
                username=ssh_user,
                port=ssh_port,
                banner_timeout=banner_timeout,
                # pkey=key
            )
            break
        except paramiko.ssh_exception.SSHException:
            print(
                "  get_connection: paramiko.ssh_exception.SSHException received. continuing..."
            )
            time.sleep(2)
    return ssh


# not used
def is_port_open(ssh_host, ssh_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((ssh_host, ssh_port))
    sock.close()
    if result == 0:
        return True
    else:
        return False


# not used
def wait_until_port_ready(ssh_host, ssh_port):
    while True:
        if is_port_open(ssh_host, ssh_port):
            break
        time.sleep(5)


# def wait_until_tcp_connection_is_stable(
#     host_to_test=SSH_HOST, port_to_test=SSH_PORT, interval=3, count=5
# ):
#     # check connection is stable to hopefully avoid connection reset issues
#     while True:
#         host = tcpping(host_to_test, port=port_to_test, interval=interval, count=count)
#         if host.is_alive and host.packet_loss == 0:
#             break
#         time.sleep(2)


# def wait_until_no_connection_resets(wait_time=5, host=SSH_HOST, port=SSH_PORT):
#     while True:
#         try:
#             with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#                 s.connect((SSH_HOST, SSH_PORT))
#                 time.sleep(wait_time)
#             break
#         except socket.SocketError as e:
#             if e.errno != errno.ECONNRESET:
#                 print(
#                     "  wait_until_no_connection_resets: connection reset. retrying..."
#                 )
#             else:
#                 print(e)
#                 print("  wait_until_no_connection_resets: other exception. retrying...")
#             time.sleep(2)
