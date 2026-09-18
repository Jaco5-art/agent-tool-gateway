"""Trusted supervisor uploaded to E2B. Never run this module on the host."""
import ctypes
import json
import os
import platform
from pathlib import Path
import resource
import selectors
import signal
import subprocess
import sys
import time


def install_no_network():
    """Linux syscall filter, inherited across exec/fork. Fail closed on unsupported ABI.

    No sockets are inherited by the generated program (close_fds=True).
    Block socket creation/connect and io_uring's alternate submission path.
    This supplements the VM boundary; it is not a general syscall sandbox.
    """
    machine=platform.machine().lower()
    if machine in ('x86_64','amd64'):
        arch=0xc000003e;blocked=[41,42,53,101,310,311,321,425,426,427,438]
    elif machine in ('aarch64','arm64'):
        arch=0xc00000b7;blocked=[198,199,203,117,270,271,280,425,426,427,438]
    else: raise RuntimeError('NETWORK_FILTER_UNSUPPORTED_ARCH')
    class Insn(ctypes.Structure):
        _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_=[('length',ctypes.c_ushort),('filter',ctypes.POINTER(Insn))]
    # LD arch; JEQ expected; otherwise KILL_PROCESS; LD syscall number.
    rules=[(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),(0x20,0,0,0)]
    # Reject x32 and future/compat high-number ABIs rather than misinterpret them.
    rules += [(0x35,0,1,512),(0x06,0,0,0x00050001)]
    for number in blocked:
        rules += [(0x15,0,1,number),(0x06,0,0,0x00050001)]
    rules.append((0x06,0,0,0x7fff0000))
    instructions=(Insn*len(rules))(*(Insn(*r) for r in rules))
    program=Program(len(rules),instructions)
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(38,1,0,0,0)!=0: raise RuntimeError('NO_NEW_PRIVILEGES_FAILED')
    if libc.prctl(22,2,ctypes.byref(program),0,0)!=0:
        raise RuntimeError('NETWORK_FILTER_INSTALL_FAILED')


def limits():
    for kind,value in [(resource.RLIMIT_AS,256*1024*1024),(resource.RLIMIT_CPU,4),
                       (resource.RLIMIT_NPROC,16),(resource.RLIMIT_NOFILE,32),
                       (resource.RLIMIT_FSIZE,1024*1024),(resource.RLIMIT_CORE,0)]:
        resource.setrlimit(kind,(value,value))
    if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0)!=0:
        raise RuntimeError('NO_NEW_PRIVILEGES_FAILED')
    install_no_network()


def main():
    buffers=[bytearray(),bytearray()];reason=None
    proc=subprocess.Popen([sys.executable,'-I','-B','/tmp/gateway/code.py'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd='/tmp/gateway/work',env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'},
        user=65534,group=65534,extra_groups=[],close_fds=True,start_new_session=True,preexec_fn=limits)
    sel=selectors.DefaultSelector()
    for i,pipe in enumerate((proc.stdout,proc.stderr)):
        os.set_blocking(pipe.fileno(),False);sel.register(pipe,selectors.EVENT_READ,i)
    deadline=time.monotonic()+8
    try:
        while sel.get_map():
            if time.monotonic()>deadline: reason='ANALYSIS_TIMEOUT';break
            for key,_ in sel.select(timeout=.05):
                chunk=os.read(key.fileobj.fileno(),4096)
                if not chunk: sel.unregister(key.fileobj);continue
                room=65536-sum(map(len,buffers))
                if len(chunk)>room: reason='ANALYSIS_OUTPUT_LIMIT';break
                buffers[key.data].extend(chunk)
            if reason: break
        if reason is None:
            try: proc.wait(timeout=max(.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: reason='ANALYSIS_TIMEOUT'
    finally:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait();sel.close();proc.stdout.close();proc.stderr.close()
    if reason is None and proc.returncode!=0: reason='ANALYSIS_CODE_FAILED'
    result=None
    if reason is None:
        try:
            def invalid_constant(value): raise ValueError(value)
            result=json.loads(buffers[0],parse_constant=invalid_constant)
            if not isinstance(result,dict): raise ValueError('object required')
        except (ValueError,UnicodeError,RecursionError): reason='ANALYSIS_INVALID_JSON'
    # Raw stderr is not echoed: exception text could contain sensitive row content.
    print(json.dumps({'ok':reason is None,'reason':reason,'result':result},allow_nan=False))

if __name__=='__main__': main()
