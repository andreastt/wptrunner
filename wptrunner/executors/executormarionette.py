# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import hashlib
import os
import socket
import threading
import time
import traceback
import urlparse
import uuid
from collections import defaultdict

from ..wpttest import WdspecResult, WdspecSubtestResult

marionette = None
nose = None

here = os.path.join(os.path.split(__file__)[0])

from .. import webdriver
from .base import (ExecutorException,
                   Protocol,
                   RefTestExecutor,
                   RefTestImplementation,
                   TestExecutor,
                   TestharnessExecutor,
                   testharness_result_converter,
                   reftest_result_converter,
                   strip_server,
                   WdspecExecutor)
from ..testrunner import Stop

# Extra timeout to use after internal test timeout at which the harness
# should force a timeout
extra_timeout = 5 # seconds


def do_delayed_imports():
    global marionette
    global errors
    global nose

    # Marionette client used to be called marionette, recently it changed
    # to marionette_driver for unfathomable reasons
    try:
        import marionette
        from marionette import errors
    except ImportError:
        from marionette_driver import marionette, errors

    import nose


class MarionetteProtocol(Protocol):
    def __init__(self, executor, browser):
        do_delayed_imports()

        Protocol.__init__(self, executor, browser)
        self.marionette = None
        self.marionette_port = browser.marionette_port

    def setup(self, runner):
        """Connect to browser via Marionette."""
        Protocol.setup(self, runner)

        self.logger.debug("Connecting to Marionette on port %i" % self.marionette_port)
        self.marionette = marionette.Marionette(host='localhost', port=self.marionette_port)

        # XXX Move this timeout somewhere
        self.logger.debug("Waiting for Marionette connection")
        while True:
            success = self.marionette.wait_for_port(60)
            #When running in a debugger wait indefinitely for firefox to start
            if success or self.executor.debug_info is None:
                break

        session_started = False
        if success:
            try:
                self.logger.debug("Starting Marionette session")
                self.marionette.start_session()
            except Exception as e:
                self.logger.warning("Starting marionette session failed: %s" % e)
            else:
                self.logger.debug("Marionette session started")
                session_started = True

        if not success or not session_started:
            self.logger.warning("Failed to connect to Marionette")
            self.executor.runner.send_message("init_failed")
        else:
            try:
                self.after_connect()
            except Exception:
                self.logger.warning("Post-connection steps failed")
                self.logger.error(traceback.format_exc())
                self.executor.runner.send_message("init_failed")
            else:
                self.executor.runner.send_message("init_succeeded")

    def teardown(self):
        try:
            self.marionette.delete_session()
        except Exception:
            # This is typically because the session never started
            pass
        del self.marionette

    @property
    def is_alive(self):
        """Check if the Marionette connection is still active."""
        try:
            self.marionette.current_window_handle
        except Exception:
            return False
        return True

    def after_connect(self):
        self.load_runner("http")

    def load_runner(self, protocol):
        # Check if we previously had a test window open, and if we did make sure it's closed
        self.marionette.execute_script("if (window.wrappedJSObject.win) {window.wrappedJSObject.win.close()}")
        url = urlparse.urljoin(self.executor.server_url(protocol), "/testharness_runner.html")
        self.logger.debug("Loading %s" % url)
        try:
            self.marionette.navigate(url)
        except Exception as e:
            self.logger.critical(
                "Loading initial page %s failed. Ensure that the "
                "there are no other programs bound to this port and "
                "that your firewall rules or network setup does not "
                "prevent access.\e%s" % (url, traceback.format_exc(e)))
        self.marionette.execute_script(
            "document.title = '%s'" % threading.current_thread().name.replace("'", '"'))

    def wait(self):
        while True:
            try:
                self.marionette.execute_async_script("");
            except errors.ScriptTimeoutException:
                pass
            except (socket.timeout, IOError):
                break
            except Exception as e:
                self.logger.error(traceback.format_exc(e))
                break

    def on_environment_change(self, old_environment, new_environment):
        #Unset all the old prefs
        for name in old_environment.get("prefs", {}).iterkeys():
            value = self.executor.original_pref_values[name]
            if value is None:
                self.clear_user_pref(name)
            else:
                self.set_pref(name, value)

        for name, value in new_environment.get("prefs", {}).iteritems():
            self.executor.original_pref_values[name] = self.get_pref(name)
            self.set_pref(name, value)

    def set_pref(self, name, value):
        if value.lower() not in ("true", "false"):
            try:
                int(value)
            except ValueError:
                value = "'%s'" % value
        else:
            value = value.lower()

        self.logger.info("Setting pref %s (%s)" % (name, value))

        script = """
            let prefInterface = Components.classes["@mozilla.org/preferences-service;1"]
                                          .getService(Components.interfaces.nsIPrefBranch);
            let pref = '%s';
            let type = prefInterface.getPrefType(pref);
            let value = %s;
            switch(type) {
                case prefInterface.PREF_STRING:
                    prefInterface.setCharPref(pref, value);
                    break;
                case prefInterface.PREF_BOOL:
                    prefInterface.setBoolPref(pref, value);
                    break;
                case prefInterface.PREF_INT:
                    prefInterface.setIntPref(pref, value);
                    break;
            }
            """ % (name, value)
        with self.marionette.using_context(self.marionette.CONTEXT_CHROME):
            self.marionette.execute_script(script)

    def clear_user_pref(self, name):
        self.logger.info("Clearing pref %s" % (name))
        script = """
            let prefInterface = Components.classes["@mozilla.org/preferences-service;1"]
                                          .getService(Components.interfaces.nsIPrefBranch);
            let pref = '%s';
            prefInterface.clearUserPref(pref);
            """ % name
        with self.marionette.using_context(self.marionette.CONTEXT_CHROME):
            self.marionette.execute_script(script)

    def get_pref(self, name):
        script = """
            let prefInterface = Components.classes["@mozilla.org/preferences-service;1"]
                                          .getService(Components.interfaces.nsIPrefBranch);
            let pref = '%s';
            let type = prefInterface.getPrefType(pref);
            switch(type) {
                case prefInterface.PREF_STRING:
                    return prefInterface.getCharPref(pref);
                case prefInterface.PREF_BOOL:
                    return prefInterface.getBoolPref(pref);
                case prefInterface.PREF_INT:
                    return prefInterface.getIntPref(pref);
                case prefInterface.PREF_INVALID:
                    return null;
            }
            """ % name
        with self.marionette.using_context(self.marionette.CONTEXT_CHROME):
            self.marionette.execute_script(script)


class RemoteMarionetteProtocol(Protocol):
    def __init__(self, executor, browser):
        do_delayed_imports()
        Protocol.__init__(self, executor, browser)
        self.session = None
        self.webdriver_binary = executor.webdriver_binary
        self.marionette_port = browser.marionette_port

    def setup(self, runner):
        """Connect to browser via the Marionette HTTP server."""
        try:
            self.server = webdriver.GeckoDriverServer(
                self.logger, self.marionette_port, binary=self.webdriver_binary)
            self.server.start(block=False)
            self.logger.info("WebDriver HTTP server listening at %s" % self.server.url)

            self.logger.info("Establishing new WebDriver session with %s" % self.server.url)
            self.session = webdriver.Session(self.server.url)
            self.session.start()
        except Exception:
            self.logger.error(traceback.format_exc())
            self.executor.runner.send_message("init_failed")
        else:
            self.executor.runner.send_message("init_succeeded")

    def teardown(self):
        try:
            self.session.end()
        except Exception:
            pass
        if self.server.is_alive:
            self.server.stop()

    @property
    def is_alive(self):
        """Test that the Marionette connection is still alive.

        Because the remote communication happens over HTTP we need to
        make an explicit request to the remote.
        """

        try:
            self.session.window.handle
        except IOError:
            return False
        return True


class ExecuteAsyncRun(object):
    def __init__(self, logger, func, marionette, url, timeout):
        self.logger = logger
        self.result = None
        self.marionette = marionette
        self.func = func
        self.url = url
        self.timeout = timeout
        self.result_flag = threading.Event()

    def run(self):
        timeout = self.timeout

        try:
            if timeout is not None:
                self.marionette.set_script_timeout((timeout + extra_timeout) * 1000)
            else:
                # We just want it to never time out, really, but marionette doesn't
                # make that possible. It also seems to time out immediately if the
                # timeout is set too high. This works at least.
                self.marionette.set_script_timeout(2**31 - 1)
        except IOError:
            self.logger.error("Lost marionette connection before starting test")
            return Stop

        executor = threading.Thread(target = self._run)
        executor.start()

        if timeout is not None:
            wait_timeout = timeout + 2 * extra_timeout
        else:
            wait_timeout = None

        flag = self.result_flag.wait(wait_timeout)
        if self.result is None:
            self.logger.debug("Timed out waiting for a result")
            assert not flag
            self.result = False, ("EXTERNAL-TIMEOUT", None)

        return self.result

    def _run(self):
        try:
            self.result = True, self.func(self.marionette, self.url, self.timeout)
        except errors.ScriptTimeoutException:
            self.logger.debug("Got a marionette timeout")
            self.result = False, ("EXTERNAL-TIMEOUT", None)
        except (socket.timeout, IOError):
            # This can happen on a crash
            # Also, should check after the test if the firefox process is still running
            # and otherwise ignore any other result and set it to crash
            self.result = False, ("CRASH", None)
        except Exception as e:
            message = getattr(e, "message", "")
            if message:
                message += "\n"
            message += traceback.format_exc(e)
            self.result = False, ("ERROR", e)

        finally:
            self.result_flag.set()


class MarionetteTestharnessExecutor(TestharnessExecutor):
    def __init__(self, browser, server_config, timeout_multiplier=1,
                 close_after_done=True, debug_info=None, **kwargs):
        """Marionette-based executor for testharness.js tests"""
        TestharnessExecutor.__init__(self, browser, server_config,
                                     timeout_multiplier=timeout_multiplier,
                                     debug_info=debug_info)

        self.protocol = MarionetteProtocol(self, browser)
        self.script = open(os.path.join(here, "testharness_marionette.js")).read()
        self.close_after_done = close_after_done
        self.window_id = str(uuid.uuid4())

        self.original_pref_values = {}

        if marionette is None:
            do_delayed_imports()

    def is_alive(self):
        return self.protocol.is_alive

    def on_environment_change(self, new_environment):
        self.protocol.on_environment_change(self.last_environment, new_environment)

        if new_environment["protocol"] != self.last_environment["protocol"]:
            self.protocol.load_runner(new_environment["protocol"])

    def do_test(self, test):
        timeout = (test.timeout * self.timeout_multiplier if self.debug_info is None
                   else None)

        success, data = ExecuteAsyncRun(self.logger,
                                      self.do_testharness,
                                      self.protocol.marionette,
                                      self.test_url(test),
                                      timeout).run()
        if success:
            return self.convert_result(test, data)

        return (test.result_cls(*data), [])

    def do_testharness(self, marionette, url, timeout):
        if self.close_after_done:
            marionette.execute_script("if (window.wrappedJSObject.win) {window.wrappedJSObject.win.close()}")

        if timeout is not None:
            timeout_ms = str(timeout * 1000)
        else:
            timeout_ms = "null"

        script = self.script % {"abs_url": url,
                                "url": strip_server(url),
                                "window_id": self.window_id,
                                "timeout_multiplier": self.timeout_multiplier,
                                "timeout": timeout_ms,
                                "explicit_timeout": timeout is None}

        return marionette.execute_async_script(script, new_sandbox=False)


class MarionetteRefTestExecutor(RefTestExecutor):
    def __init__(self, browser, server_config, timeout_multiplier=1,
                 screenshot_cache=None, close_after_done=True,
                 debug_info=None, **kwargs):

        """Marionette-based executor for reftests"""
        RefTestExecutor.__init__(self,
                                 browser,
                                 server_config,
                                 screenshot_cache=screenshot_cache,
                                 timeout_multiplier=timeout_multiplier,
                                 debug_info=debug_info)
        self.protocol = MarionetteProtocol(self, browser)
        self.implementation = RefTestImplementation(self)
        self.close_after_done = close_after_done
        self.has_window = False
        self.original_pref_values = {}

        with open(os.path.join(here, "reftest.js")) as f:
            self.script = f.read()
        with open(os.path.join(here, "reftest-wait.js")) as f:
            self.wait_script = f.read()

    def is_alive(self):
        return self.protocol.is_alive

    def on_environment_change(self, new_environment):
        self.protocol.on_environment_change(self.last_environment, new_environment)

    def do_test(self, test):
        if self.close_after_done and self.has_window:
            self.protocol.marionette.close()
            self.protocol.marionette.switch_to_window(
                self.protocol.marionette.window_handles[-1])
            self.has_window = False

        if not self.has_window:
            self.protocol.marionette.execute_script(self.script)
            self.protocol.marionette.switch_to_window(self.protocol.marionette.window_handles[-1])
            self.has_window = True

        result = self.implementation.run_test(test)
        return self.convert_result(test, result)

    def screenshot(self, test, viewport_size, dpi):
        # https://github.com/w3c/wptrunner/issues/166
        assert viewport_size is None
        assert dpi is None

        timeout =  self.timeout_multiplier * test.timeout if self.debug_info is None else None

        test_url = self.test_url(test)

        return ExecuteAsyncRun(self.logger,
                             self._screenshot,
                             self.protocol.marionette,
                             test_url,
                             timeout).run()

    def _screenshot(self, marionette, url, timeout):
        marionette.navigate(url)

        marionette.execute_async_script(self.wait_script)

        screenshot = marionette.screenshot()
        # strip off the data:img/png, part of the url
        if screenshot.startswith("data:image/png;base64,"):
            screenshot = screenshot.split(",", 1)[1]

        return screenshot


class WdspecRun(object):
    def __init__(self, func, session, path, timeout):
        self.func = func
        self.result = None
        self.session = session
        self.path = path
        self.timeout = timeout
        self.result_flag = threading.Event()

    def run(self):
        """Runs function in a thread and interrupts it if it exceeds the
        given timeout.  Returns (True, (Result, [SubtestResult ...])) in
        case of success, or (False, (status, extra information)) in the
        event of failure.
        """

        executor = threading.Thread(target=self._run)
        executor.start()

        flag = self.result_flag.wait(self.timeout)
        if self.result is None:
            assert not flag
            self.result = False, ("EXTERNAL-TIMEOUT", None)

        return self.result

    def _run(self):
        try:
            self.result = True, self.func(self.session, self.path, self.timeout)
        except (socket.timeout, IOError):
            self.result = False, ("CRASH", None)
        except Exception as e:
            message = getattr(e, "message")
            if message:
                message += "\n"
            message += traceback.format_exc(e)
            self.result = False, ("ERROR", message)
        finally:
            self.result_flag.set()


class MarionetteWdspecExecutor(WdspecExecutor):
    def __init__(self, browser, server_config, webdriver_binary,
                 timeout_multiplier=1, close_after_done=True, debug_info=None):
        WdspecExecutor.__init__(self, browser, server_config,
                                timeout_multiplier=timeout_multiplier,
                                debug_info=debug_info)
        self.webdriver_binary = webdriver_binary
        self.protocol = RemoteMarionetteProtocol(self, browser)

        if nose is None:
            do_delayed_imports()

    def is_alive(self):
        return self.protocol.is_alive

    def on_environment_change(self, new_environment):
        pass

    def do_test(self, test):
        timeout = test.timeout * self.timeout_multiplier + extra_timeout

        success, data = WdspecRun(self.do_wdspec,
                                  self.protocol.session,
                                  test.path,
                                  timeout).run()

        if success:
            return self.convert_result(test, data)

        return (test.result_cls(*data), [])

    def do_wdspec(self, session, path, timeout):
        class StreamRedirecter(nose.plugins.Plugin):
            """Redirects the output stream of nosetests to the given
            stream, or if no stream is given, to `/dev/null`.
            """

            name = "stream-redirecter"
            enabled = True

            def __init__(self, redirect=None):
                nose.plugins.Plugin.__init__(self)
                self.fh = redirect or open(os.devnull, "w")

            def setOutputStream(self, stream):
                return self

            def write(self, s):
                self.fh.write(s)

            def writeln(self, line=""):
                self.fh.write("%s\n" % line)

            def flush(self):
                self.fh.flush()

        class SubtestResultRecorder(nose.plugins.Plugin):
            """Maps test results to accepted ``wpttest`` test results."""

            name = "subtest-result-recorder"
            enabled = True

            def __init__(self):
                nose.plugins.Plugin.__init__(self)
                self.results = []

            def addSuccess(self, test):
                self.record_result(test, "PASS")

            def addFailure(self, test, failure):
                _, message, exc = failure
                stack = traceback.format_exc(exc)
                self.record_result(test, "FAIL", message, stack=stack)

            def addError(self, test, error):
                _, message, exc = error
                stack = traceback.format_exc(exc)
                self.record_result(test, "ERROR", message, stack=stack)

            def addSkip(self, test):
                self.record_result(test, "ERROR",
                    "In-test skip decorators are disallowed, "
                    "please use WPT metadata to ignore tests.")

            def record_result(self, test, status, message=None, stack=None):
                new_result = (test.id(), status, message, stack)
                self.results.append(new_result)

        class SessionAdapter(nose.plugins.Plugin):
            """Exposes a `session` global in the scope of context
            functions in the test.
            """

            name = "session-adapter"
            enabled = True

            def __init__(self, session):
                nose.plugins.Plugin.__init__(self)
                self.session = session

            def startContext(self, context):
                context.session = self.session

        silence = StreamRedirecter()
        recorder = SubtestResultRecorder()
        adapter = SessionAdapter(session)

        nose.core.TestProgram(
            exit=False,
            defaultTest=path,
            addplugins=[silence, adapter, recorder],
            argv=["nosetests",
                  "--nocapture",
                  "--with-stream-redirecter",
                  "--with-subtest-result-recorder",
                  "--with-session-adapter"])

        harness_result = ("OK", None)
        subtest_results = recorder.results
        return (harness_result, subtest_results)
