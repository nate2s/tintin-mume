# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import print_function

import socket
try:
	import ssl
except ImportError:
	ssl = None
from telnetlib import IAC, GA, DONT, DO, WONT, WILL, theNULL, SB, SE, TTYPE, NAWS
import threading

from .config import Config, config_lock
from .mapper import USER_DATA, MUD_DATA, Mapper
from .mpi import MPI
from .utils import iterRange, unescapeXML


CHARSET = chr(42).encode("us-ascii")
SB_REQUEST, SB_ACCEPTED, SB_REJECTED, SB_TTABLE_IS, SB_TTABLE_REJECTED, SB_TTABLE_ACK, SB_TTABLE_NAK = (chr(i).encode("us-ascii") for i in iterRange(1, 8))

class Proxy(threading.Thread):
	def __init__(self, client, server, mapper):
		threading.Thread.__init__(self)
		self.name = "Proxy"
		self._client = client
		self._server = server
		self._mapper = mapper
		self.alive = threading.Event()

	def close(self):
		self.alive.clear()

	def run(self):
		userCommands = [func[len("user_command_"):].encode("us-ascii", "ignore") for func in dir(self._mapper) if func.startswith("user_command_")]
		self.alive.set()
		while self.alive.isSet():
			try:
				data = self._client.recv(4096)
			except socket.timeout:
				continue
			except EnvironmentError:
				self.close()
				continue
			if not data:
				self.close()
			elif data.strip() and data.strip().split()[0] in userCommands:
				self._mapper.queue.put((USER_DATA, data))
			else:
				try:
					self._server.sendall(data)
				except EnvironmentError:
					self.close()
					continue


class Server(threading.Thread):
	def __init__(self, client, server, mapper, outputFormat, interface):
		threading.Thread.__init__(self)
		self.name = "Server"
		self._client = client
		self._server = server
		self._mapper = mapper
		self._outputFormat = outputFormat
		self._interface = interface
		self.alive = threading.Event()

	def close(self):
		self.alive.clear()

	def run(self):
		self.alive.set()
		normalFormat = self._outputFormat == "normal"
		tinTinFormat = self._outputFormat == "tintin"
		rawFormat = self._outputFormat == "raw"
		ignoreBytes = frozenset([ord(theNULL), 0x11])
		negotiationBytes = frozenset(ord(byte) for byte in [DONT, DO, WONT, WILL])
		ordIAC = ord(IAC)
		ordGA = ord(GA)
		ordSB = ord(SB)
		ordSE = ord(SE)
		ordLF = ord("\n")
		ordCHARSET = ord(CHARSET)
		charsetSep = b";"
		charsets = {
			"ascii": b"US-ASCII",
			"latin-1": b"ISO-8859-1",
			"utf-8": b"UTF-8"
		}
		defaultCharset = charsets["ascii"]
		inIAC = False
		inSubOption = False
		inCharset = False
		inCharsetResponse = False
		inMPI = False
		mpiThreads = []
		mpiCounter = 0
		mpiCommand = None
		mpiLen = None
		mpiBuffer = bytearray()
		clientBuffer = bytearray()
		tagBuffer = bytearray()
		textBuffer = bytearray()
		charsetResponseBuffer = bytearray()
		charsetResponseCode = None
		readingTag = False
		inGratuitous = False
		modeNone = 0
		modeRoom = 1
		modeName = 2
		modeDescription = 3
		modeExits = 4
		modePrompt = 5
		modeTerrain = 6
		xmlMode = modeNone
		tagReplacements = {
			b"prompt": b"PROMPT:",
			b"/prompt": b":PROMPT",
			b"name": b"NAME:",
			b"/name": b":NAME",
			b"tell": b"TELL:",
			b"/tell": b":TELL",
			b"narrate": b"NARRATE:",
			b"/narrate": b":NARRATE",
			b"pray": b"PRAY:",
			b"/pray": b":PRAY",
			b"say": b"SAY:",
			b"/say": b":SAY",
			b"emote": b"EMOTE:",
			b"/emote": b":EMOTE"
		}
		initialOutput = b"".join((IAC, DO, TTYPE, IAC, DO, NAWS))
		encounteredInitialOutput = False
		while self.alive.isSet():
			try:
				data = self._server.recv(4096)
			except EnvironmentError:
				self.close()
				continue
			if not data:
				self.close()
				continue
			elif not encounteredInitialOutput and data.startswith(initialOutput):
				# The connection to Mume has been established, and the game has just responded with the login screen.
				# Identify for Mume Remote Editing.
				self._server.sendall(b"~$#EI\n")
				# Turn on XML mode.
				self._server.sendall(b"~$#EX2\n3G\n")
				# Tell the Mume server to put IAC-GA at end of prompts.
				self._server.sendall(b"~$#EP2\nG\n")
				# Tell the server that we will negotiate the character set.
				self._server.sendall(IAC + WILL + CHARSET)
				inCharset = True
				encounteredInitialOutput = True
			for byte in bytearray(data):
				if inIAC:
					clientBuffer.append(byte)
					if byte in negotiationBytes:
						# This is the second byte in a 3-byte telnet option sequence.
						# Skip the byte, and move on to the next.
						continue
					# From this point on, byte is the final byte in a 2-3 byte telnet option sequence.
					inIAC = False
					if byte == ordSB:
						# Sub-option negotiation begin
						inSubOption = True
					elif byte == ordSE:
						# Sub-option negotiation end
						if inCharset and inCharsetResponse:
							# IAC SE was erroneously added to the client buffer. Remove it.
							del clientBuffer[-2:]
							charsetResponseCode = None
							del charsetResponseBuffer[:]
							inCharsetResponse = False
							inCharset = False
						inSubOption = False
					elif inSubOption:
						# Ignore subsequent bytes until the sub option negotiation has ended.
						continue
					elif byte == ordIAC:
						# This is an escaped IAC byte to be added to the buffer.
						mpiCounter = 0
						if inMPI:
							mpiBuffer.append(byte)
							# IAC + IAC was appended to the client buffer earlier.
							# It must be removed as MPI data should not be sent to the mud client.
							del clientBuffer[-2:]
					elif byte == ordCHARSET and inCharset and clientBuffer[-3:] == IAC + DO + CHARSET:
						# Negotiate the character set.
						self._server.sendall(IAC + SB + CHARSET + SB_REQUEST + charsetSep + defaultCharset + IAC + SE)
						# IAC + DO + CHARSET was appended to the client buffer earlier.
						# It must be removed as character set negotiation data should not be sent to the mud client.
						del clientBuffer[-3:]
					elif byte == ordGA:
						self._mapper.queue.put((MUD_DATA, ("iac_ga", b"")))
						if xmlMode == modeNone:
							textBuffer.append(ordLF)
				elif byte == ordIAC:
					clientBuffer.append(byte)
					inIAC = True
				elif inSubOption or byte in ignoreBytes:
					if byte == ordCHARSET and inCharset and clientBuffer[-2:] == IAC + SB:
						# Character set negotiation responses should *not* be sent to the client.
						del clientBuffer[-2:]
						inCharsetResponse = True
					elif inCharsetResponse and byte not in ignoreBytes:
						if charsetResponseCode is None:
							charsetResponseCode = byte
						else:
							charsetResponseBuffer.append(byte)
					else:
						clientBuffer.append(byte)
				elif inMPI:
					if byte == ordLF and mpiCommand is None and mpiLen is None:
						# The first line of MPI data was recieved.
						# The first byte is the MPI command, E for edit, V for view.
						# The remaining byte sequence is the length of the MPI data to be received.
						if mpiBuffer[0:1] in (b"E", b"V") and mpiBuffer[1:].isdigit():
							mpiCommand = mpiBuffer[0:1]
							mpiLen = int(mpiBuffer[1:])
						else:
							# Invalid MPI command or length.
							inMPI = False
						del mpiBuffer[:]
					else:
						mpiBuffer.append(byte)
						if mpiLen is not None and len(mpiBuffer) >= mpiLen:
							# The last byte in the MPI data has been reached.
							mpiThreads.append(MPI(client=self._client, server=self._server, isTinTin=tinTinFormat, command=mpiCommand, data=bytes(mpiBuffer)))
							mpiThreads[-1].start()
							del mpiBuffer[:]
							mpiCommand = None
							mpiLen = None
							inMPI = False
				elif byte == 126 and mpiCounter == 0 and clientBuffer.endswith(b"\n") or byte == 36 and mpiCounter == 1 or byte == 35 and mpiCounter == 2:
					# Byte is one of the first 3 bytes in the 4-byte MPI sequence (~$#E).
					mpiCounter += 1
				elif byte == 69 and mpiCounter == 3:
					# Byte is the final byte in the 4-byte MPI sequence (~$#E).
					inMPI = True
					mpiCounter = 0
				elif readingTag:
					mpiCounter = 0
					if byte == 62: # >
						# End of XML tag reached.
						if xmlMode == modeNone:
							if tagBuffer.startswith(b"exits"):
								xmlMode = modeExits
							elif tagBuffer.startswith(b"prompt"):
								xmlMode = modePrompt
							elif tagBuffer.startswith(b"room"):
								xmlMode = modeRoom
							elif tagBuffer.startswith(b"movement"):
								self._mapper.queue.put((MUD_DATA, ("movement", bytes(tagBuffer)[8:].replace(b" dir=", b"", 1).split(b"/", 1)[0])))
						elif xmlMode == modeRoom:
							if tagBuffer.startswith(b"name"):
								xmlMode = modeName
							elif tagBuffer.startswith(b"description"):
								xmlMode = modeDescription
							elif tagBuffer.startswith(b"terrain"):
								# Terrain tag only comes up in blindness or fog
								xmlMode = modeTerrain
							elif tagBuffer.startswith(b"gratuitous"):
								inGratuitous = True
							elif tagBuffer.startswith(b"/gratuitous"):
								inGratuitous = False
							elif tagBuffer.startswith(b"/room"):
								xmlMode = modeNone
						elif xmlMode == modeName and tagBuffer.startswith(b"/name"):
							xmlMode = modeRoom
						elif xmlMode == modeDescription and tagBuffer.startswith(b"/description"):
							xmlMode = modeRoom
						elif xmlMode == modeTerrain and tagBuffer.startswith(b"/terrain"):
							xmlMode = modeRoom
						elif xmlMode == modeExits and tagBuffer.startswith(b"/exits"):
							xmlMode = modeNone
						elif xmlMode == modePrompt and tagBuffer.startswith(b"/prompt"):
							xmlMode = modeNone
						if tinTinFormat:
							clientBuffer.extend(tagReplacements.get(bytes(tagBuffer), b""))
						del tagBuffer[:]
						readingTag = False
					else:
						tagBuffer.append(byte)
					if rawFormat:
						clientBuffer.append(byte)
				elif byte == 60: # <
					# Start of new XML tag.
					mpiCounter = 0
					readingTag = True
					text = bytes(textBuffer)
					del textBuffer[:]
					if rawFormat:
						clientBuffer.append(byte)
					if not text.strip():
						continue
					elif xmlMode == modeNone:
						for line in text.splitlines():
							if line.strip():
								self._mapper.queue.put((MUD_DATA, ("line", line)))
					elif xmlMode == modeName:
						self._mapper.queue.put((MUD_DATA, ("name", text)))
					elif xmlMode == modeDescription:
						self._mapper.queue.put((MUD_DATA, ("description", text)))
					elif xmlMode == modeRoom:
						self._mapper.queue.put((MUD_DATA, ("dynamic", text)))
					elif xmlMode == modeExits:
						self._mapper.queue.put((MUD_DATA, ("exits", text)))
					elif xmlMode == modePrompt:
						self._mapper.queue.put((MUD_DATA, ("prompt", text)))
				else:
					# Byte is not part of a Telnet negotiation, MPI negotiation, or XML tag name.
					mpiCounter = 0
					textBuffer.append(byte)
					if rawFormat or not inGratuitous:
						clientBuffer.append(byte)
			data = bytes(clientBuffer)
			if not rawFormat:
				data = unescapeXML(data, isbytes=True).replace(b"\r", b"").replace(b"\n\n", b"\n")
			try:
				self._client.sendall(data)
			except EnvironmentError:
				self.close()
				continue
			del clientBuffer[:]
		if self._interface != "text":
			# Shutdown the gui
			with self._mapper._gui_queue_lock:
				self._mapper._gui_queue.put(None)
		# Join the MPI threads (if any) before joining the Mapper thread.
		for mpiThread in mpiThreads:
			mpiThread.join()


def main(outputFormat, interface):
	outputFormat = outputFormat.strip().lower()
	interface = interface.strip().lower()
	if interface != "text":
		try:
			import pyglet
		except ImportError:
			print("Unable to find pyglet. Disabling the GUI")
			interface = "text"
	proxySocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	proxySocket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
	proxySocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	proxySocket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
	proxySocket.bind(("", 4000))
	proxySocket.listen(1)
	clientConnection, proxyAddress = proxySocket.accept()
	clientConnection.settimeout(1.0)
	serverConnection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	serverConnection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
	serverConnection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
	if ssl is not None:
		serverConnection = ssl.wrap_socket(serverConnection, cert_reqs=ssl.CERT_REQUIRED, ca_certs="cacert.pem", ssl_version=ssl.PROTOCOL_TLS)
	try:
		serverConnection.connect(("193.134.218.98", 443))

	except TimeoutError:
		try:
			clientConnection.sendall(b"\r\nError: server connection timed out!\r\n")
			clientConnection.sendall(b"\r\n")
			clientConnection.shutdown(socket.SHUT_RDWR)
		except EnvironmentError:
			pass
		clientConnection.close()
		return
	if ssl is not None:
		# Validating server identity with ssl module
		# See https://wiki.python.org/moin/SSL
		for field in serverConnection.getpeercert()["subject"]:
			if field[0][0] == "commonName":
				certhost = field[0][1]
				if certhost != "mume.org":
					raise ssl.SSLError("Host name 'mume.org' doesn't match certificate host '{}'".format(certhost))
	mapperThread = Mapper(client=clientConnection, server=serverConnection, outputFormat=outputFormat, interface=interface)
	proxyThread = Proxy(client=clientConnection, server=serverConnection, mapper=mapperThread)
	serverThread = Server(client=clientConnection, server=serverConnection, mapper=mapperThread, outputFormat=outputFormat, interface=interface)
	serverThread.start()
	proxyThread.start()
	mapperThread.start()
	if interface != "text":
		pyglet.app.run()
	serverThread.join()
	try:
		serverConnection.shutdown(socket.SHUT_RDWR)
	except EnvironmentError:
		pass
	mapperThread.queue.put((None, None))
	mapperThread.join()
	try:
		clientConnection.sendall(b"\r\n")
		proxyThread.close()
		clientConnection.shutdown(socket.SHUT_RDWR)
	except EnvironmentError:
		pass
	proxyThread.join()
	serverConnection.close()
	clientConnection.close()
