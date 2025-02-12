"""
per-test stdout/stderr capturing mechanism.

"""
from __future__ import absolute_import, division, print_function

import contextlib
import sys
import os
import io
from io import UnsupportedOperation
from tempfile import TemporaryFile

import py
import pytest
from _pytest.compat import CaptureIO

unicode = py.builtin.text

patchsysdict = {0: 'stdin', 1: 'stdout', 2: 'stderr'}


def pytest_addoption(parser):
    group = parser.getgroup("general")
    group._addoption(
        '--capture', action="store",
        default="fd" if hasattr(os, "dup") else "sys",
        metavar="method", choices=['fd', 'sys', 'no'],
        help="per-test capturing method: one of fd|sys|no.")
    group._addoption(
        '-s', action="store_const", const="no", dest="capture",
        help="shortcut for --capture=no.")


@pytest.hookimpl(hookwrapper=True)
def pytest_load_initial_conftests(early_config, parser, args):
    ns = early_config.known_args_namespace
    if ns.capture == "fd":
        _py36_windowsconsoleio_workaround(sys.stdout)
    _colorama_workaround()
    _readline_workaround()
    pluginmanager = early_config.pluginmanager
    capman = CaptureManager(ns.capture)
    pluginmanager.register(capman, "capturemanager")

    # make sure that capturemanager is properly reset at final shutdown
    early_config.add_cleanup(capman.reset_capturings)

    # make sure logging does not raise exceptions at the end
    def silence_logging_at_shutdown():
        if "logging" in sys.modules:
            sys.modules["logging"].raiseExceptions = False
    early_config.add_cleanup(silence_logging_at_shutdown)

    # finally trigger conftest loading but while capturing (issue93)
    capman.init_capturings()
    outcome = yield
    out, err = capman.suspendcapture()
    if outcome.excinfo is not None:
        sys.stdout.write(out)
        sys.stderr.write(err)


class CaptureManager:
    def __init__(self, method):
        self._method = method

    def _getcapture(self, method):
        if method == "fd":
            return MultiCapture(out=True, err=True, Capture=FDCapture)
        elif method == "sys":
            return MultiCapture(out=True, err=True, Capture=SysCapture)
        elif method == "no":
            return MultiCapture(out=False, err=False, in_=False)
        else:
            raise ValueError("unknown capturing method: %r" % method)

    def init_capturings(self):
        assert not hasattr(self, "_capturing")
        self._capturing = self._getcapture(self._method)
        self._capturing.start_capturing()

    def reset_capturings(self):
        cap = self.__dict__.pop("_capturing", None)
        if cap is not None:
            cap.pop_outerr_to_orig()
            cap.stop_capturing()

    def resumecapture(self):
        self._capturing.resume_capturing()

    def suspendcapture(self, in_=False):
        self.deactivate_funcargs()
        cap = getattr(self, "_capturing", None)
        if cap is not None:
            try:
                outerr = cap.readouterr()
            finally:
                cap.suspend_capturing(in_=in_)
            return outerr

    def activate_funcargs(self, pyfuncitem):
        capfuncarg = pyfuncitem.__dict__.pop("_capfuncarg", None)
        if capfuncarg is not None:
            capfuncarg._start()
            self._capfuncarg = capfuncarg

    def deactivate_funcargs(self):
        capfuncarg = self.__dict__.pop("_capfuncarg", None)
        if capfuncarg is not None:
            capfuncarg.close()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(self, collector):
        if isinstance(collector, pytest.File):
            self.resumecapture()
            outcome = yield
            out, err = self.suspendcapture()
            rep = outcome.get_result()
            if out:
                rep.sections.append(("Captured stdout", out))
            if err:
                rep.sections.append(("Captured stderr", err))
        else:
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        self.resumecapture()
        yield
        self.suspendcapture_item(item, "setup")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.resumecapture()
        self.activate_funcargs(item)
        yield
        # self.deactivate_funcargs() called from suspendcapture()
        self.suspendcapture_item(item, "call")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item):
        self.resumecapture()
        yield
        self.suspendcapture_item(item, "teardown")

    @pytest.hookimpl(tryfirst=True)
    def pytest_keyboard_interrupt(self, excinfo):
        self.reset_capturings()

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(self, excinfo):
        self.reset_capturings()

    def suspendcapture_item(self, item, when, in_=False):
        out, err = self.suspendcapture(in_=in_)
        item.add_report_section(when, "stdout", out)
        item.add_report_section(when, "stderr", err)


error_capsysfderror = "cannot use capsys and capfd at the same time"


@pytest.fixture
def capsys(request):
    """Enable capturing of writes to sys.stdout/sys.stderr and make
    captured output available via ``capsys.readouterr()`` method calls
    which return a ``(out, err)`` tuple.
    """
    if "capfd" in request.fixturenames:
        raise request.raiseerror(error_capsysfderror)
    request.node._capfuncarg = c = CaptureFixture(SysCapture, request)
    return c


@pytest.fixture
def capfd(request):
    """Enable capturing of writes to file descriptors 1 and 2 and make
    captured output available via ``capfd.readouterr()`` method calls
    which return a ``(out, err)`` tuple.
    """
    if "capsys" in request.fixturenames:
        request.raiseerror(error_capsysfderror)
    if not hasattr(os, 'dup'):
        pytest.skip("capfd funcarg needs os.dup")
    request.node._capfuncarg = c = CaptureFixture(FDCapture, request)
    return c


class CaptureFixture:
    def __init__(self, captureclass, request):
        self.captureclass = captureclass
        self.request = request

    def _start(self):
        self._capture = MultiCapture(out=True, err=True, in_=False,
                                     Capture=self.captureclass)
        self._capture.start_capturing()

    def close(self):
        cap = self.__dict__.pop("_capture", None)
        if cap is not None:
            self._outerr = cap.pop_outerr_to_orig()
            cap.stop_capturing()

    def readouterr(self):
        try:
            return self._capture.readouterr()
        except AttributeError:
            return self._outerr

    @contextlib.contextmanager
    def disabled(self):
        capmanager = self.request.config.pluginmanager.getplugin('capturemanager')
        capmanager.suspendcapture_item(self.request.node, "call", in_=True)
        try:
            yield
        finally:
            capmanager.resumecapture()


def safe_text_dupfile(f, mode, default_encoding="UTF8"):
    """ return a open text file object that's a duplicate of f on the
        FD-level if possible.
    """
    encoding = getattr(f, "encoding", None)
    try:
        fd = f.fileno()
    except Exception:
        if "b" not in getattr(f, "mode", "") and hasattr(f, "encoding"):
            # we seem to have a text stream, let's just use it
            return f
    else:
        newfd = os.dup(fd)
        if "b" not in mode:
            mode += "b"
        f = os.fdopen(newfd, mode, 0)  # no buffering
    return EncodedFile(f, encoding or default_encoding)


class EncodedFile(object):
    errors = "strict"  # possibly needed by py3 code (issue555)

    def __init__(self, buffer, encoding):
        self.buffer = buffer
        self.encoding = encoding

    def write(self, obj):
        if isinstance(obj, unicode):
            obj = obj.encode(self.encoding, "replace")
        self.buffer.write(obj)

    def writelines(self, linelist):
        data = ''.join(linelist)
        self.write(data)

    @property
    def name(self):
        """Ensure that file.name is a string."""
        return repr(self.buffer)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "buffer"), name)


class MultiCapture(object):
    out = err = in_ = None

    def __init__(self, out=True, err=True, in_=True, Capture=None):
        if in_:
            self.in_ = Capture(0)
        if out:
            self.out = Capture(1)
        if err:
            self.err = Capture(2)

    def start_capturing(self):
        if self.in_:
            self.in_.start()
        if self.out:
            self.out.start()
        if self.err:
            self.err.start()

    def pop_outerr_to_orig(self):
        """ pop current snapshot out/err capture and flush to orig streams. """
        out, err = self.readouterr()
        if out:
            self.out.writeorg(out)
        if err:
            self.err.writeorg(err)
        return out, err

    def suspend_capturing(self, in_=False):
        if self.out:
            self.out.suspend()
        if self.err:
            self.err.suspend()
        if in_ and self.in_:
            self.in_.suspend()
            self._in_suspended = True

    def resume_capturing(self):
        if self.out:
            self.out.resume()
        if self.err:
            self.err.resume()
        if hasattr(self, "_in_suspended"):
            self.in_.resume()
            del self._in_suspended

    def stop_capturing(self):
        """ stop capturing and reset capturing streams """
        if hasattr(self, '_reset'):
            raise ValueError("was already stopped")
        self._reset = True
        if self.out:
            self.out.done()
        if self.err:
            self.err.done()
        if self.in_:
            self.in_.done()

    def readouterr(self):
        """ return snapshot unicode value of stdout/stderr capturings. """
        return (self.out.snap() if self.out is not None else "",
                self.err.snap() if self.err is not None else "")


class NoCapture:
    __init__ = start = done = suspend = resume = lambda *args: None


class FDCapture:
    """ Capture IO to/from a given os-level filedescriptor. """

    def __init__(self, targetfd, tmpfile=None):
        self.targetfd = targetfd
        try:
            self.targetfd_save = os.dup(self.targetfd)
        except OSError:
            self.start = lambda: None
            self.done = lambda: None
        else:
            if targetfd == 0:
                assert not tmpfile, "cannot set tmpfile with stdin"
                tmpfile = open(os.devnull, "r")
                self.syscapture = SysCapture(targetfd)
            else:
                if tmpfile is None:
                    f = TemporaryFile()
                    with f:
                        tmpfile = safe_text_dupfile(f, mode="wb+")
                if targetfd in patchsysdict:
                    self.syscapture = SysCapture(targetfd, tmpfile)
                else:
                    self.syscapture = NoCapture()
            self.tmpfile = tmpfile
            self.tmpfile_fd = tmpfile.fileno()

    def __repr__(self):
        return f"<FDCapture {self.targetfd} oldfd={self.targetfd_save}>"

    def start(self):
        """ Start capturing on targetfd using memorized tmpfile. """
        try:
            os.fstat(self.targetfd_save)
        except (AttributeError, OSError):
            raise ValueError("saved filedescriptor not valid anymore")
        os.dup2(self.tmpfile_fd, self.targetfd)
        self.syscapture.start()

    def snap(self):
        f = self.tmpfile
        f.seek(0)
        if res := f.read():
            enc = getattr(f, "encoding", None)
            if enc and isinstance(res, bytes):
                res = py.builtin._totext(res, enc, "replace")
            f.truncate(0)
            f.seek(0)
            return res
        return ''

    def done(self):
        """ stop capturing, restore streams, return original capture file,
        seeked to position zero. """
        targetfd_save = self.__dict__.pop("targetfd_save")
        os.dup2(targetfd_save, self.targetfd)
        os.close(targetfd_save)
        self.syscapture.done()
        self.tmpfile.close()

    def suspend(self):
        self.syscapture.suspend()
        os.dup2(self.targetfd_save, self.targetfd)

    def resume(self):
        self.syscapture.resume()
        os.dup2(self.tmpfile_fd, self.targetfd)

    def writeorg(self, data):
        """ write to original file descriptor. """
        if py.builtin._istext(data):
            data = data.encode("utf8")  # XXX use encoding of original stream
        os.write(self.targetfd_save, data)


class SysCapture:
    def __init__(self, fd, tmpfile=None):
        name = patchsysdict[fd]
        self._old = getattr(sys, name)
        self.name = name
        if tmpfile is None:
            tmpfile = DontReadFromInput() if name == "stdin" else CaptureIO()
        self.tmpfile = tmpfile

    def start(self):
        setattr(sys, self.name, self.tmpfile)

    def snap(self):
        f = self.tmpfile
        res = f.getvalue()
        f.truncate(0)
        f.seek(0)
        return res

    def done(self):
        setattr(sys, self.name, self._old)
        del self._old
        self.tmpfile.close()

    def suspend(self):
        setattr(sys, self.name, self._old)

    def resume(self):
        setattr(sys, self.name, self.tmpfile)

    def writeorg(self, data):
        self._old.write(data)
        self._old.flush()


class DontReadFromInput:
    """Temporary stub class.  Ideally when stdin is accessed, the
    capturing should be turned off, with possibly all data captured
    so far sent to the screen.  This should be configurable, though,
    because in automated test runs it is better to crash than
    hang indefinitely.
    """

    encoding = None

    def read(self, *args):
        raise IOError("reading from stdin while output is captured")
    readline = read
    readlines = read
    __iter__ = read

    def fileno(self):
        raise UnsupportedOperation("redirected stdin is pseudofile, "
                                   "has no fileno()")

    def isatty(self):
        return False

    def close(self):
        pass

    @property
    def buffer(self):
        if sys.version_info >= (3, 0):
            return self
        else:
            raise AttributeError('redirected stdin has no attribute buffer')


def _colorama_workaround():
    """
    Ensure colorama is imported so that it attaches to the correct stdio
    handles on Windows.

    colorama uses the terminal on import time. So if something does the
    first import of colorama while I/O capture is active, colorama will
    fail in various ways.
    """

    if not sys.platform.startswith('win32'):
        return
    with contextlib.suppress(ImportError):
        import colorama  # noqa


def _readline_workaround():
    """
    Ensure readline is imported so that it attaches to the correct stdio
    handles on Windows.

    Pdb uses readline support where available--when not running from the Python
    prompt, the readline module is not imported until running the pdb REPL.  If
    running pytest with the --pdb option this means the readline module is not
    imported until after I/O capture has been started.

    This is a problem for pyreadline, which is often used to implement readline
    support on Windows, as it does not attach to the correct handles for stdout
    and/or stdin if they have been redirected by the FDCapture mechanism.  This
    workaround ensures that readline is imported before I/O capture is setup so
    that it can attach to the actual stdin/out for the console.

    See https://github.com/pytest-dev/pytest/pull/1281
    """

    if not sys.platform.startswith('win32'):
        return
    with contextlib.suppress(ImportError):
        import readline  # noqa


def _py36_windowsconsoleio_workaround(stream):
    """
    Python 3.6 implemented unicode console handling for Windows. This works
    by reading/writing to the raw console handle using
    ``{Read,Write}ConsoleW``.

    The problem is that we are going to ``dup2`` over the stdio file
    descriptors when doing ``FDCapture`` and this will ``CloseHandle`` the
    handles used by Python to write to the console. Though there is still some
    weirdness and the console handle seems to only be closed randomly and not
    on the first call to ``CloseHandle``, or maybe it gets reopened with the
    same handle value when we suspend capturing.

    The workaround in this case will reopen stdio with a different fd which
    also means a different handle by replicating the logic in
    "Py_lifecycle.c:initstdio/create_stdio".

    :param stream: in practice ``sys.stdout`` or ``sys.stderr``, but given
        here as parameter for unittesting purposes.

    See https://github.com/pytest-dev/py/issues/103
    """
    if not sys.platform.startswith('win32') or sys.version_info[:2] < (3, 6):
        return

    # bail out if ``stream`` doesn't seem like a proper ``io`` stream (#2666)
    if not hasattr(stream, 'buffer'):
        return

    buffered = hasattr(stream.buffer, 'raw')
    raw_stdout = stream.buffer.raw if buffered else stream.buffer

    if not isinstance(raw_stdout, io._WindowsConsoleIO):
        return

    def _reopen_stdio(f, mode):
        buffering = 0 if not buffered and mode[0] == 'w' else -1
        return io.TextIOWrapper(
            open(os.dup(f.fileno()), mode, buffering),
            f.encoding,
            f.errors,
            f.newlines,
            f.line_buffering)

    sys.__stdin__ = sys.stdin = _reopen_stdio(sys.stdin, 'rb')
    sys.__stdout__ = sys.stdout = _reopen_stdio(sys.stdout, 'wb')
    sys.__stderr__ = sys.stderr = _reopen_stdio(sys.stderr, 'wb')
