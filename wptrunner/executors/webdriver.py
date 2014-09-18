import os
import errno
import time
import socket
import urlparse
import traceback

import mozprocess

from ..browsers.base import get_free_port, cmd_arg


class RemoteServer(object):
	def __init__(self, logger, url):
		self.url = url

	@property
	def alive(self):
		# TODO: Implement ping
		pass


class LocalServer(object):
	used_ports = set()

	def __init__(self, logger, binary, port=None):
		self.logger = logger
		self.binary = binary
		self.port = port
		if self.port is None:
			self.port = get_free_port(4444, exclude=self.used_ports)
		self.used_ports.add(self.port)
		self.proc, self.cmd = None, None
		self._url = None

	def start(self):
		self.proc = mozprocess.ProcessHandler(self.cmd, processOutputLine=self.on_output)
		self.proc.run()
		
	def stop(self):
		if self.proc is not None and hasattr(self.proc, "proc"):
			self.proc.kill()
			
	@property
	def alive(self):
		return self.pid is not None
		
	def on_output(self, line):
		self.logger.process_output(self.pid,
			line.decode("utf8", "replace"),
			command=" ".join(self.cmd))

	@property
	def pid(self):
		if self.proc is not None:
			return self.proc.pid

	@property
	def url(self):
		if self._url is None:
			#self._url = urlparse.urlparse("http://127.0.0.1:%i%s" % (self.port, self.endpoint))
			self._url = "http://127.0.0.1:%i%s" % (self.port, self.endpoint)
		return self._url


class SeleniumLocalServer(LocalServer):
	def __init__(self, logger, binary, port=None):
		LocalServer.__init__(self, logger, binary, port=port)
		self.endpoint = "/wd/hub"
		# TODO: verbose logging
		self.cmd = ["java",
			"-jar", self.binary,
			"-port", str(self.port)]
	
	def start(self):
		self.logger.info("Starting local Selenium server")
		LocalServer.start(self)
		self.logger.debug("Waiting for server to become accessible: %s" % self.url)
		surl = urlparse.urlparse(self.url)
		addr = (surl.hostname, surl.port)
		try:
			wait_service(addr, timeout=10)
		except:
			self.logger.warning("Server was not accessible within the timeout:\n%s" % traceback.format_exc())
		else:
			self.logger.debug("Server is running")
	
	def stop(self):
		self.logger.info("Stopping local Selenium server")
		LocalServer.stop(self)


class ChromedriverLocalServer(LocalServer):
	def __init__(self, logger, binary, port=None, endpoint="/session"):
		LocalServer.__init__(self, logger, binary, port=port)
		self.endpoint = endpoint
		# TODO: verbose logging
		self.cmd = [self.binary,
			cmd_arg("port", str(self.port)),
			cmd_arg("url-base", self.endpoint)]
		
	def start(self):
		self.logger.info("Starting local chromedriver server")
		LocalServer.start(self)
		
	def stop(self):
		self.logger.info("Stopping local chromedriver server")
		LocalServer.stop(self)


def wait_service2(addr, timeout=3):
	"""Waits until network service given as a tuple of (host, port) becomes available or the `timeout`
	duration is reached, at which point an exception is raised."""
	so = socket.socket()
	end = time.time() + timeout
	while end < time.time():
		try:
			next_timeout = end - time.time()
			if next_timeout < 0:
				raise "Timed out waiting for service: %s" % addr
			else:
				so.settimeout(next_timeout)
			so.connect(addr)
		except socket.timeout:
			raise "Timed out waiting for service: %s" % addr
		except socket.error as e:
			print e
			if type(e.args) is not tuple or e[0] != errno.ETIMEDOUT:
				raise
		finally:
			so.close()


def wait_service3(addr, timeout=None):
	found = False
	so = socket.socket()
	if timeout is not None:
		end = time.time() + timeout
	while True:
		if end < time.time():
			break
		try:
			so.connect(addr)
			found = True
		except socket.timeout:
			pass
		except socket.error as e:
			if type(e.args) is not tuple or e[0] != errno.ETIMEDOUT:
				raise
		finally:
			so.close()
	if not found:
		raise "Service is unavailable"


def wait_service(addr, timeout=30):
	so = socket.socket()
	so.settimeout(float(timeout))
	so.connect(addr)
	so.close()
