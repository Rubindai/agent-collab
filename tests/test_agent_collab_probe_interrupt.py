"""Offline process-lifetime regressions for availability probes."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_agent_collab_runtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule

availability = fixtures.load_availability()


@unittest.skipUnless(os.name == 'posix', 'POSIX process groups')
class ProbeInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.processes = []
        self.original_popen = subprocess.Popen
        self.handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        self.popen_patch = mock.patch.object(availability.subprocess, 'Popen', side_effect=self.record_popen)
        self.popen_patch.start()

    def record_popen(self, *args, **kwargs):
        process = self.original_popen(*args, **kwargs)
        self.processes.append(process)
        return process

    def tearDown(self):
        self.popen_patch.stop()
        for sig, original in self.handlers.items():
            self.assertEqual(signal.getsignal(sig), original)
        for process in self.processes:
            if availability.SAFETY.process_group_alive(process.pid):
                availability.SAFETY.terminate_process_group(process.pid)
            process.wait(timeout=1)

    def assert_cleaned(self):
        self.assertTrue(self.processes)
        for process in self.processes:
            self.assertIsNotNone(process.returncode, 'Popen leader was not reaped')
            self.assertFalse(availability.SAFETY.process_group_alive(process.pid))
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)

    def run_probe(self, code, **kwargs):
        return availability.run_bounded_command(
            [sys.executable, '-c', code], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=kwargs.pop('timeout', 2), **kwargs)

    def test_normal_completion_and_stdin(self):
        result = self.run_probe('import sys;print(sys.stdin.read());print("diagnostic",file=sys.stderr)', input='hello')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'hello\n')
        self.assertEqual(result.stderr, 'diagnostic\n')
        self.assert_cleaned()

    def test_nonzero_preserves_check_failure(self):
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.run_probe('import sys;print("failure");sys.exit(7)', check=True)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.output, 'failure\n')
        self.assert_cleaned()

    def test_timeout_cleans_and_reaps(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_probe('import time;time.sleep(60)', timeout=0.05)
        self.assert_cleaned()

    def test_output_limit_cleans_and_reaps(self):
        with mock.patch.object(availability, 'MAX_PROBE_OUTPUT_BYTES', 1024):
            with self.assertRaisesRegex(OSError, 'output exceeded'):
                self.run_probe('import sys,time;sys.stdout.write("x"*8192);sys.stdout.flush();time.sleep(60)')
        self.assert_cleaned()

    def interrupt_once(self, exc):
        real_sleep = time.sleep
        calls = [0]
        def sleep(seconds):
            calls[0] += 1
            if calls[0] == 1:
                raise exc
            return real_sleep(seconds)
        return sleep

    def test_keyboardinterrupt_during_supervision(self):
        interruption = KeyboardInterrupt('original interrupt')
        with mock.patch.object(availability.time, 'sleep', side_effect=self.interrupt_once(interruption)):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.run_probe('import time;time.sleep(60)')
        self.assertIs(raised.exception, interruption)
        self.assert_cleaned()

    def test_keyboardinterrupt_during_identity_capture(self):
        interruption = KeyboardInterrupt('startup interrupt')
        with mock.patch.object(availability.SAFETY, 'process_identity', side_effect=interruption):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.run_probe('raise SystemExit("must not launch")', input='payload')
        self.assertIs(raised.exception, interruption)
        self.assert_cleaned()

    def signal_once(self, signum):
        real_sleep = time.sleep
        sent = [False]
        def sleep(seconds):
            if not sent[0]:
                sent[0] = True
                os.kill(os.getpid(), signum)
            return real_sleep(seconds)
        return sleep

    def test_sigterm_during_supervision(self):
        with mock.patch.object(availability.time, 'sleep', side_effect=self.signal_once(signal.SIGTERM)):
            with self.assertRaises(SystemExit) as raised:
                self.run_probe('import time;time.sleep(60)')
        self.assertEqual(raised.exception.code, 143)
        self.assert_cleaned()

    def test_sigint_during_supervision(self):
        with mock.patch.object(availability.time, 'sleep', side_effect=self.signal_once(signal.SIGINT)):
            with self.assertRaises(KeyboardInterrupt):
                self.run_probe('import time;time.sleep(60)')
        self.assert_cleaned()

    def test_signals_during_popen_return_do_not_release_gate(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / 'provider-started'
                def signalled_popen(*args, **kwargs):
                    process = self.record_popen(*args, **kwargs)
                    os.kill(os.getpid(), sig)
                    return process
                with mock.patch.object(availability.subprocess, 'Popen', side_effect=signalled_popen):
                    expected = SystemExit if sig == signal.SIGTERM else KeyboardInterrupt
                    with self.assertRaises(expected):
                        self.run_probe(f'from pathlib import Path;Path({str(marker)!r}).write_text("launched")')
                self.assertFalse(marker.exists())
                self.assert_cleaned()

    def test_original_runtime_error_is_preserved(self):
        failure = OSError('original capture failure')
        with mock.patch.object(availability.os, 'fstat', side_effect=failure):
            with self.assertRaises(OSError) as raised:
                self.run_probe('import time;time.sleep(60)')
        self.assertIs(raised.exception, failure)
        self.assert_cleaned()

    def test_cleanup_failure_overrides_original_with_context(self):
        interruption = KeyboardInterrupt('original interrupt')
        real_cleanup = availability.SAFETY.terminate_process_group
        def failed_receipt(pgid):
            cleanup = real_cleanup(pgid)
            return {**cleanup, 'quiescent': False, 'outcome': 'injected_cleanup_failure'}
        with mock.patch.object(availability.time, 'sleep', side_effect=self.interrupt_once(interruption)), mock.patch.object(
            availability.SAFETY, 'terminate_process_group', side_effect=failed_receipt
        ):
            with self.assertRaisesRegex(OSError, 'injected_cleanup_failure') as raised:
                self.run_probe('import time;time.sleep(60)')
        self.assertIs(raised.exception.__context__, interruption)
        self.assert_cleaned()

    def test_writer_start_failure_preserves_original(self):
        failure = RuntimeError('writer could not start')
        with mock.patch.object(availability.threading.Thread, 'start', side_effect=failure):
            with self.assertRaises(RuntimeError) as raised:
                self.run_probe('import time;time.sleep(60)', input='payload')
        self.assertIs(raised.exception, failure)
        self.assert_cleaned()

    def test_signal_during_cleanup_waits_for_cleanup(self):
        real_cleanup = availability.SAFETY.terminate_process_group
        def interrupted_cleanup(pgid):
            os.kill(os.getpid(), signal.SIGTERM)
            return real_cleanup(pgid)
        with mock.patch.object(availability.SAFETY, 'terminate_process_group', side_effect=interrupted_cleanup):
            with self.assertRaises(SystemExit) as raised:
                self.run_probe('print("done")')
        self.assertEqual(raised.exception.code, 143)
        self.assert_cleaned()

    def test_startup_failure_restores_handlers(self):
        failure = OSError('Popen failed')
        with mock.patch.object(availability.subprocess, 'Popen', side_effect=failure):
            with self.assertRaises(OSError) as raised:
                self.run_probe('')
        self.assertIs(raised.exception, failure)
        self.assertFalse(self.processes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
