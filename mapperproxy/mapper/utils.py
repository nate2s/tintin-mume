﻿# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import print_function

import os.path
import re
import subprocess
import sys
import textwrap

from . import terminalsize


ANSI_COLOR_REGEX = re.compile(r"\x1b\[[\d;]+m")
WHITE_SPACE_REGEX = re.compile(r"\s+", flags=re.UNICODE)
ESCAPE_XML_STR_ENTITIES = (
	("&", "&amp;"),
	("<", "&lt;"),
	(">", "&gt;"),
	("\"", "&quot;"),
	("'", "&#39;"),
	("'", "&apos;")
)
UNESCAPE_XML_STR_ENTITIES = tuple((second, first) for first, second in ESCAPE_XML_STR_ENTITIES)
ESCAPE_XML_BYTES_ENTITIES = tuple((first.encode("us-ascii"), second.encode("us-ascii")) for first, second in ESCAPE_XML_STR_ENTITIES)
UNESCAPE_XML_BYTES_ENTITIES = tuple((second, first) for first, second in ESCAPE_XML_BYTES_ENTITIES)

def stripAnsi(data):
	return ANSI_COLOR_REGEX.sub("", data)

def simplified(data):
	return WHITE_SPACE_REGEX.sub(" ", data).strip()

def humanSort(listToSort):
	return sorted(listToSort, key=lambda item: [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", item, re.UNICODE)])

def regexFuzzy(data):
	if not data:
		return ""
	elif isinstance(data, str):
		return "(".join(list(data)) + ")?" * (len(data) - 1)
	elif isinstance(data, list):
		return "|".join("(".join(list(item)) + ")?" * (len(item) - 1) for item in data)

def getDirectoryPath(directory):
	# This is needed for py2exe
	try:
		if sys.frozen or sys.importers:
			return os.path.join(os.path.dirname(sys.executable), directory)
	except AttributeError:
		return os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", directory)

def iterItems(dictionary, **kw):
	try:
		return iter(dictionary.iteritems(**kw))
	except AttributeError:
		return iter(dictionary.items(**kw))

def iterRange(*args):
	try:
		return iter(xrange(*args))
	except NameError:
		return iter(range(*args))

def multiReplace(data, replacements):
	try:
		replacements = iterItems(replacements)
	except AttributeError:
		# replacements is a list of tuples.
		pass
	for pattern, substitution in replacements:
		data = data.replace(pattern, substitution)
	return data

def escapeXML(data, isbytes=False):
	return multiReplace(data, ESCAPE_XML_BYTES_ENTITIES if isbytes else ESCAPE_XML_STR_ENTITIES)

def unescapeXML(data, isbytes=False):
	return multiReplace(data, UNESCAPE_XML_BYTES_ENTITIES if isbytes else UNESCAPE_XML_STR_ENTITIES)

def decodeBytes(data):
	try:
		return data.decode("utf-8")
	except UnicodeDecodeError:
		return data.decode("latin-1")
	except AttributeError:
		return ""

def page(lines):
	"""Output word wrapped lines using the 'more' shell command if necessary."""
	lines = "\n".join(lines).splitlines()
	width, height = terminalsize.get_terminal_size()
	# Word wrapping to 1 less than the terminal width is necessary to prevent occasional blank lines in the terminal output.
	text = "\n".join(textwrap.fill(line.strip(), width - 1) for line in lines)
	if text.count("\n") +1 < height:
		print(text)
	else:
		more = subprocess.Popen("more", stdin=subprocess.PIPE, shell=True)
		more.stdin.write(text.encode("utf-8"))
		more.stdin.close()
		more.wait()
