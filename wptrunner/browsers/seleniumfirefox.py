import os
import urlparse

import mozprocess

from .base import get_free_port, Browser, ExecutorBrowser, require_arg, cmd_arg
from ..executors import webdriver
from ..executors.executorselenium import SeleniumTestharnessExecutor, required_files


here = os.path.split(__file__)[0]

__wptrunner__ = {"product": "seleniumfirefox",
	"check_args": "check_args",
	"browser": "FirefoxBrowser",
	"executor": {"testharness": "SeleniumTestharnessExecutor"},
	"browser_kwargs": "browser_kwargs",
	"executor_kwargs": "executor_kwargs",
	"env_options": "env_options"}


def check_args(**kwargs):
	require_arg(kwargs, "binary")


def browser_kwargs(**kwargs):
	return {"binary": kwargs["binary"]}


def executor_kwargs(http_server_url, **kwargs):
	from selenium import webdriver
	timeout_multiplier = kwargs["timeout_multiplier"] or 1
	return {"http_server_url": http_server_url,
		"timeout_multiplier": timeout_multiplier,
		"capabilities": webdriver.DesiredCapabilities.FIREFOX}


def env_options():
	return {"host": "localhost",
		"bind_hostname": "true",
		"required_files": required_files}


class FirefoxBrowser(Browser):
	def __init__(self, logger, binary):
		Browser.__init__(self, logger)
		self.binary = binary
		self.server = webdriver.SeleniumLocalServer(logger, "/Users/ato/selenium-server-standalone.jar")
		self.webdriver_url = self.server.url
	
	def start(self):
		self.server.start()

	def stop(self):
		self.server.stop()
		
	def is_alive(self):
		return self.server.alive

	def cleanup(self):
		self.stop()

	def executor_browser(self):
		return ExecutorBrowser, {"webdriver_url": self.server.url}
