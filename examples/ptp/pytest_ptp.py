# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Unlicense OR CC0-1.0
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

import pexpect
import pytest

from pytest_embedded import Dut

# Set to an interface name such as 'enp192s0' for local debug. Leave empty to auto-detect.
TARGET_IF = ''

# Written to a temporary file and passed to ptp4l -f.
# Lower value wins BMCA. The DUT announces priority1 and priority2 128, so this host is selected as master.
PTP4L_CONFIG = """\
[global]
time_stamping software
priority1 1
priority2 1
"""

SYNC_SETTLE_S = 90
OFFSET_LIMIT_NS = 30_000  # Loose because of software timestamping on a VM/Docker host.
OFFSET_CONSECUTIVE = 3
PTP4L_STARTUP_S = 1
IP_TIMEOUT_S = 60


def find_target_if(my_if: str = '') -> str:
    target_if = ''
    netifs = os.listdir('/sys/class/net/')
    netifs.sort(reverse=True)
    logging.info(f'detected interfaces: {str(netifs)}')

    if my_if == '':
        if 'dut_p1' in netifs:
            target_if = 'dut_p1'
        else:
            for netif in netifs:
                if netif.startswith(('eth', 'enp', 'eno')):
                    target_if = netif
                    break
    elif my_if in netifs:
        target_if = my_if

    if target_if == '':
        raise RuntimeError('network interface not found')
    logging.info('Use %s for testing', target_if)
    return target_if


def _ptp4l_executable() -> str:
    found = shutil.which('ptp4l')
    if found is not None:
        return found
    for candidate in ('/usr/sbin/ptp4l', '/sbin/ptp4l'):
        if os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError('ptp4l not found; install the linuxptp package')


def _write_ptp4l_config() -> str:
    fd, config_path = tempfile.mkstemp(prefix='ptp4l-', suffix='.conf')
    try:
        with os.fdopen(fd, 'w') as config_file:
            config_file.write(PTP4L_CONFIG)
    except OSError:
        os.unlink(config_path)
        raise
    return config_path


def _ptp4l_command(executable: str, interface: str, config_path: str) -> list[str]:
    command = [executable, '-i', interface, '-2', '-m', '-f', config_path]
    # /etc/apparmor.d/usr.sbin.ptp4l lets /usr/sbin/ptp4l read configs only under /etc/linuxptp.
    # aa-exec -p unconfined skips that profile so this temporary file can be opened.
    aa_exec = shutil.which('aa-exec')
    if aa_exec is not None:
        return [aa_exec, '-p', 'unconfined', '--', *command]
    return command


def _log_ptp4l(stream: IO[str]) -> None:
    for line in stream:
        logging.info(f'ptp4l: {line.rstrip()}')


def _ensure_ptp4l_running(proc: subprocess.Popen[str]) -> None:
    return_code = proc.poll()
    if return_code is not None:
        raise RuntimeError(f'ptp4l exited with code {return_code}')


@contextmanager
def _run_ptp4l(interface: str) -> Iterator[subprocess.Popen[str]]:
    executable = _ptp4l_executable()
    config_path = _write_ptp4l_config()

    proc: subprocess.Popen[str] | None = None
    log_thread: threading.Thread | None = None
    try:
        command = _ptp4l_command(executable, interface, config_path)
        logging.info(f'Starting {" ".join(command)}')
        proc = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.stdout is None:
            raise RuntimeError('ptp4l stdout was not captured')
        log_thread = threading.Thread(target=_log_ptp4l, args=(proc.stdout,), daemon=True)
        log_thread.start()
        time.sleep(PTP4L_STARTUP_S)
        _ensure_ptp4l_running(proc)
        yield proc
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if log_thread is not None:
            log_thread.join(timeout=2)
        os.unlink(config_path)


def _offset_converged(dut: Dut, proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + SYNC_SETTLE_S
    consecutive = 0
    best_abs: int | None = None
    last_offset: int | None = None
    while time.monotonic() < deadline:
        _ensure_ptp4l_running(proc)
        remaining = max(deadline - time.monotonic(), 0.1)
        match = dut.expect([r'ptpd: offset_ns (-?\d+)', pexpect.TIMEOUT], timeout=remaining)
        if not hasattr(match, 'group'):  # TIMEOUT: settle window ended
            break
        offset_ns = int(match.group(1).decode())
        last_offset = offset_ns
        best_abs = abs(offset_ns) if best_abs is None else min(best_abs, abs(offset_ns))
        # Bounds |offset| vs master only, not peak-to-peak sample jitter
        # (e.g. +LIMIT then -LIMIT both pass even though the step is 2*LIMIT).
        if abs(offset_ns) <= OFFSET_LIMIT_NS:
            consecutive += 1
            if consecutive >= OFFSET_CONSECUTIVE:
                logging.info(
                    f'PTP offset within {OFFSET_LIMIT_NS} ns for {OFFSET_CONSECUTIVE} consecutive samples'
                )
                return
        else:
            consecutive = 0

    raise AssertionError(
        f'PTP offset did not reach +/-{OFFSET_LIMIT_NS} ns within {SYNC_SETTLE_S} s '
        f'(last={last_offset} ns, best_abs={best_abs} ns)'
    )


@pytest.mark.parametrize(
    'config, target',
    [
        pytest.param('defaults_esp32p4', 'esp32p4', marks=[pytest.mark.eth_ip101, pytest.mark.rev_default]),
        pytest.param('defaults_esp32p4v1', 'esp32p4', marks=[pytest.mark.eth_ip101, pytest.mark.esp32p4_rev1]),
        pytest.param('defaults_esp32s31', 'esp32s31', marks=[pytest.mark.eth_yt8531]),
    ],
    indirect=['target'],
)
def test_ptp(dut: Dut) -> None:
    interface = find_target_if(TARGET_IF)
    with _run_ptp4l(interface) as proc:
        dut.expect(r'esp_netif_handlers: .+ ip: (\d+\.\d+\.\d+\.\d+),', timeout=IP_TIMEOUT_S)
        _offset_converged(dut, proc)
