import json
import logging
import socket
import sys
import select

from celery.datastructures import ExceptionInfo

from orchestra.utils.sys import sshrun
from orchestra.utils.python import CaptureStdout, import_class

from . import settings


logger = logging.getLogger(__name__)

paramiko_connections = {}


def Paramiko(backend, log, server, cmds, async=False):
    """
    Executes cmds to remote server using Pramaiko
    """
    import paramiko
    script = '\n'.join(cmds)
    script = script.replace('\r', '')
    log.state = log.STARTED
    log.script = script
    log.save(update_fields=('script', 'state', 'updated_at'))
    if not cmds:
        return
    channel = None
    ssh = None
    try:
        addr = server.get_address()
        # ssh connection
        ssh = paramiko_connections.get(addr)
        if not ssh:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            key = settings.ORCHESTRATION_SSH_KEY_PATH
            try:
                ssh.connect(addr, username='root', key_filename=key)
            except socket.error as e:
                logger.error('%s timed out on %s' % (backend, addr))
                log.state = log.TIMEOUT
                log.stderr = str(e)
                log.save(update_fields=('state', 'stderr', 'updated_at'))
                return
            paramiko_connections[addr] = ssh
        transport = ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(backend.script_executable)
        channel.sendall(script)
        channel.shutdown_write()
        # Log results
        logger.debug('%s running on %s' % (backend, server))
        if async:
            second = False
            while True:
                # Non-blocking is the secret ingridient in the async sauce
                select.select([channel], [], [])
                if channel.recv_ready():
                    part = channel.recv(1024).decode('utf-8')
                    while part:
                        log.stdout += part
                        part = channel.recv(1024).decode('utf-8')
                if channel.recv_stderr_ready():
                    part = channel.recv_stderr(1024).decode('utf-8')
                    while part:
                        log.stderr += part
                        part = channel.recv_stderr(1024).decode('utf-8')
                log.save(update_fields=('stdout', 'stderr', 'updated_at'))
                if channel.exit_status_ready():
                    if second:
                        break
                    second = True
        else:
            log.stdout += channel.makefile('rb', -1).read().decode('utf-8')
            log.stderr += channel.makefile_stderr('rb', -1).read().decode('utf-8')
        
        log.exit_code = channel.recv_exit_status()
        log.state = log.SUCCESS if log.exit_code == 0 else log.FAILURE
        logger.debug('%s execution state on %s is %s' % (backend, server, log.state))
        log.save()
    except:
        log.state = log.ERROR
        log.traceback = ExceptionInfo(sys.exc_info()).traceback
        logger.error('Exception while executing %s on %s' % (backend, server))
        logger.debug(log.traceback)
        log.save()
    finally:
        if log.state == log.STARTED:
            log.state = log.ABORTED
            log.save(update_fields=('state', 'updated_at'))
        if channel is not None:
            channel.close()


def OpenSSH(backend, log, server, cmds, async=False):
    """
    Executes cmds to remote server using SSH with connection resuse for maximum performance
    """
    script = '\n'.join(cmds)
    script = script.replace('\r', '')
    log.state = log.STARTED
    log.script = script
    log.save(update_fields=('script', 'state', 'updated_at'))
    if not cmds:
        return
    channel = None
    ssh = None
    try:
        ssh = sshrun(server.get_address(), script, executable=backend.script_executable,
            persist=True, async=async, silent=True)
        logger.debug('%s running on %s' % (backend, server))
        if async:
            second = False
            for state in ssh:
                log.stdout += state.stdout.decode('utf8')
                log.stderr += state.stderr.decode('utf8')
                log.save(update_fields=('stdout', 'stderr', 'updated_at'))
            log.exit_code = state.exit_code
        else:
            log.stdout = ssh.stdout.decode('utf8')
            log.stderr = ssh.stderr.decode('utf8')
            log.exit_code = ssh.exit_code
        log.state = log.SUCCESS if log.exit_code == 0 else log.FAILURE
        logger.debug('%s execution state on %s is %s' % (backend, server, log.state))
        log.save()
    except:
        log.state = log.ERROR
        log.traceback = ExceptionInfo(sys.exc_info()).traceback
        logger.error('Exception while executing %s on %s' % (backend, server))
        logger.debug(log.traceback)
        log.save()
    finally:
        if log.state == log.STARTED:
            log.state = log.ABORTED
            log.save(update_fields=('state', 'updated_at'))


def SSH(*args, **kwargs):
    """ facade function enabling to chose between multiple SSH backends"""
    method = import_class(settings.ORCHESTRATION_SSH_METHOD_BACKEND)
    return method(*args, **kwargs)


def Python(backend, log, server, cmds, async=False):
    script = [ str(cmd.func.__name__) + str(cmd.args) for cmd in cmds ]
    script = json.dumps(script, indent=4).replace('"', '')
    log.state = log.STARTED
    log.script = '\n'.join((log.script, script))
    log.save(update_fields=('script', 'state', 'updated_at'))
    try:
        for cmd in cmds:
            with CaptureStdout() as stdout:
                result = cmd(server)
            for line in stdout:
                log.stdout += line + '\n'
            if async:
                log.save(update_fields=('stdout', 'updated_at'))
    except:
        log.exit_code = 1
        log.state = log.FAILURE
        log.traceback = ExceptionInfo(sys.exc_info()).traceback
        logger.error('Exception while executing %s on %s' % (backend, server))
    else:
        log.exit_code = 0
        log.state = log.SUCCESS
        logger.debug('%s execution state on %s is %s' % (backend, server, log.state))
    log.save()
