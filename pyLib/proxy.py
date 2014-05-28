#!/usr/bin/env python

import re
import socket
import threading

IGNORE_TAGS_REGEX = re.compile(br"<[/]?(?:xml|terrain|magic|weather|room|exits|header|prompt|status|song|shout|yell|social|hit|damage|enemy|familiar|snoop.*?|highlight.*?)>")
SEPARATE_TAGS_REGEX = re.compile(br"<(?P<tag>name|description|tell|say|narrate|pray|emote)>(?P<text>.*?)</(?P=tag)>", re.DOTALL|re.MULTILINE)
MOVEMENT_TAGS_REGEX = re.compile(br"<(?P<tag>movement)( dir=(?P<text>north|south|east|west|up|down))?/>")


class ProxyThread(threading.Thread):
	def __init__(self, writer, reader, type):
		threading.Thread.__init__(self)
		self._writer = writer
		self._reader = reader
		self.isServer = type=="server"
		self.isClient = type=="client"

	def run(self):
		upperMatch = lambda match: b"".join((match.group("tag").upper(), b":", match.group("text").strip().replace(b"\r\n", b" ") if match.group("text") else "", b":", match.group("tag").upper(), b"\r\n"))
		while True:
			bytes = self._reader.recv(4096)
			if not bytes:
				break
			elif self.isServer:
				bytes = IGNORE_TAGS_REGEX.sub(b"", bytes)
				bytes = SEPARATE_TAGS_REGEX.sub(upperMatch, bytes)
				bytes = MOVEMENT_TAGS_REGEX.sub(upperMatch, bytes)
				bytes = b"\r\n".join([line for line in bytes.splitlines() if line]).replace(b"&amp;", b"&").replace(b"&lt;", b"<").replace(b"&gt;", b">").replace(b"&#39;", b"'").replace(b"&quot;", b'"')
			self._writer.send(bytes)


def main():
	loc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	loc.bind(("", 4000))
	loc.listen(1)
	client, client_addr = loc.accept()
	server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	server.connect(("193.134.218.99", 4242))
	proxy_writer = ProxyThread(writer=server, reader=client, type="client")
	proxy_reader = ProxyThread(writer=client, reader=server, type="server")
	proxy_reader.start()
	proxy_writer.start()
	proxy_reader.join()
	client.send(b"\r\n")
	server.shutdown(socket.SHUT_RDWR)
	client.shutdown(socket.SHUT_RDWR)
	proxy_writer.join()
	server.close()
	client.close()

if __name__ == "__main__":
	main()
