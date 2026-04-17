#!/usr/bin/ python3

"""MCP server that exposes Penelope reverse-shell sessions to Claude Code.

Penelope must be running first — it creates ~/.penelope/mcp.sock on startup.
Register this server in .mcp.json in your Claude Code Project or globally:

    {
      "mcpServers": {
        "penelope": {
          "command": "python3",
          "args": ["/YOURPAPTH/penelope/mcp_penelope.py"]
        }
      }
    }
"""

import json
import os
import socket
import sys

SOCK_PATH = os.path.expanduser('~/.penelope/mcp.sock')
MAX_RESPONSE = 10 * 1024 * 1024  # 10 MB
MAX_CMD_LEN  = 65536              # 64 KB

# low-level IPC

def _call(method: str, **kwargs) -> dict:
	req = json.dumps({'method': method, **kwargs}) + '\n'
	with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
		try:
			s.connect(SOCK_PATH)
		except FileNotFoundError:
			return {'error': 'Penelope is not running (socket not found)'}
		except ConnectionRefusedError:
			return {'error': 'Penelope socket exists but refused connection'}
		try:
			s.sendall(req.encode())
			buf = b''
			while b'\n' not in buf:
				chunk = s.recv(65536)
				if not chunk:
					break
				buf += chunk
				if len(buf) > MAX_RESPONSE:
					return {'error': 'Response too large (> 10 MB)'}
			return json.loads(buf.split(b'\n', 1)[0])
		except OSError as e:
			return {'error': f'IPC socket error: {e}'}
		except json.JSONDecodeError as e:
			return {'error': f'Invalid JSON from Penelope: {e}'}


# MCP wire protocol (JSON-RPC 2.0 over stdio) 

def _send(obj: dict):
	sys.stdout.write(json.dumps(obj) + '\n')
	sys.stdout.flush()


def _send_result(req_id, result):
	_send({'jsonrpc': '2.0', 'id': req_id, 'result': result})


def _send_error(req_id, code: int, message: str):
	if req_id is None:
		return  # never reply to notifications
	_send({'jsonrpc': '2.0', 'id': req_id,
	       'error': {'code': code, 'message': message}})


# Tool definitions 

TOOLS = [
	{
		'name': 'list_sessions',
		'description': 'List all active reverse-shell sessions in Penelope.',
		'inputSchema': {
			'type': 'object',
			'properties': {},
			'required': [],
		},
	},
	{
		'name': 'get_session_info',
		'description': (
			'Get detailed information about a specific Penelope session '
			'(OS, shell type, user, hostname, cwd, arch).'
		),
		'inputSchema': {
			'type': 'object',
			'properties': {
				'session_id': {
					'type': 'integer',
					'description': 'The numeric session ID from list_sessions.',
				},
			},
			'required': ['session_id'],
		},
	},
	{
		'name': 'exec_in_session',
		'description': (
			'Execute a shell command in a Penelope session and return its output. '
			'WARNING: this runs arbitrary commands on the target system.'
		),
		'inputSchema': {
			'type': 'object',
			'properties': {
				'session_id': {
					'type': 'integer',
					'description': 'The numeric session ID from list_sessions.',
				},
				'command': {
					'type': 'string',
					'description': 'Shell command to run on the target.',
				},
			},
			'required': ['session_id', 'command'],
		},
	},
	{
		'name': 'kill_session',
		'description': 'Kill (close) a Penelope session.',
		'inputSchema': {
			'type': 'object',
			'properties': {
				'session_id': {
					'type': 'integer',
					'description': 'The numeric session ID from list_sessions.',
				},
			},
			'required': ['session_id'],
		},
	},
	{
		'name': 'upload_to_session',
		'description': (
			'Upload one or more local files to a Penelope session. '
			'local_path supports multiple files and globs (shlex syntax). '
			'remote_path defaults to the session current working directory.'
		),
		'inputSchema': {
			'type': 'object',
			'properties': {
				'session_id': {
					'type': 'integer',
					'description': 'The numeric session ID from list_sessions.',
				},
				'local_path': {
					'type': 'string',
					'description': 'Local file path(s) or URL(s) to upload.',
				},
				'remote_path': {
					'type': 'string',
					'description': 'Remote destination directory (optional, defaults to session cwd).',
				},
			},
			'required': ['session_id', 'local_path'],
		},
	},
	{
		'name': 'download_from_session',
		'description': (
			'Download one or more remote files from a Penelope session. '
			'remote_path supports globs. Files are saved to the session downloads folder.'
		),
		'inputSchema': {
			'type': 'object',
			'properties': {
				'session_id': {
					'type': 'integer',
					'description': 'The numeric session ID from list_sessions.',
				},
				'remote_path': {
					'type': 'string',
					'description': 'Remote file path(s) or glob(s) to download.',
				},
			},
			'required': ['session_id', 'remote_path'],
		},
	},
]


# helpers 

def _parse_session_id(req_id, args) -> int | None:
	"""Return validated int session_id, or send an error and return None."""
	raw = args.get('session_id')
	try:
		sid = int(raw)
	except (TypeError, ValueError, OverflowError):
		_send_error(req_id, -32602, 'session_id must be an integer')
		return None
	if not (0 <= sid < 2**31):
		_send_error(req_id, -32602, 'session_id out of range')
		return None
	return sid


# request handlers

def _handle_notification(_req_id, _params):
	pass  # notifications must not be replied to


def _handle_initialize(req_id, _params):
	_send_result(req_id, {
		'protocolVersion': '2024-11-05',
		'capabilities': {'tools': {}},
		'serverInfo': {'name': 'penelope', 'version': '1.0.0'},
	})


def _handle_tools_list(req_id, _params):
	_send_result(req_id, {'tools': TOOLS})


def _handle_tools_call(req_id, params):
	name = params.get('name')
	args = params.get('arguments', {})

	if name == 'list_sessions':
		resp = _call('list_sessions')

	elif name == 'get_session_info':
		sid = _parse_session_id(req_id, args)
		if sid is None:
			return
		resp = _call('get_session_info', session_id=sid)

	elif name == 'exec_in_session':
		sid = _parse_session_id(req_id, args)
		if sid is None:
			return
		cmd = args.get('command', '').strip()
		if not cmd:
			_send_error(req_id, -32602, 'command is required')
			return
		if len(cmd) > MAX_CMD_LEN:
			_send_error(req_id, -32602, f'command too long (max {MAX_CMD_LEN} bytes)')
			return
		resp = _call('exec', session_id=sid, command=cmd)

	elif name == 'kill_session':
		sid = _parse_session_id(req_id, args)
		if sid is None:
			return
		resp = _call('kill_session', session_id=sid)

	elif name == 'upload_to_session':
		sid = _parse_session_id(req_id, args)
		if sid is None:
			return
		local_path = args.get('local_path', '').strip()
		if not local_path:
			_send_error(req_id, -32602, 'local_path is required')
			return
		remote_path = args.get('remote_path') or None
		resp = _call('upload', session_id=sid, local_path=local_path, remote_path=remote_path)

	elif name == 'download_from_session':
		sid = _parse_session_id(req_id, args)
		if sid is None:
			return
		remote_path = args.get('remote_path', '').strip()
		if not remote_path:
			_send_error(req_id, -32602, 'remote_path is required')
			return
		resp = _call('download', session_id=sid, remote_path=remote_path)

	else:
		_send_error(req_id, -32601, f'unknown tool: {name}')
		return

	text = f'Error: {resp["error"]}' if 'error' in resp else json.dumps(resp, indent=2)
	_send_result(req_id, {'content': [{'type': 'text', 'text': text}]})


# main loop

HANDLERS = {
	'initialize':                _handle_initialize,
	'notifications/initialized': _handle_notification,
	'tools/list':                _handle_tools_list,
	'tools/call':                _handle_tools_call,
}


def main():
	for line in sys.stdin:
		line = line.strip()
		if not line:
			continue
		try:
			req = json.loads(line)
		except json.JSONDecodeError:
			continue

		req_id = req.get('id')
		method = req.get('method', '')
		params = req.get('params', {})

		handler = HANDLERS.get(method)
		if handler:
			try:
				handler(req_id, params)
			except Exception as e:
				_send_error(req_id, -32603, f'Internal error: {e}')
		elif req_id is not None:
			_send_error(req_id, -32601, f'method not found: {method}')


if __name__ == '__main__':
	main()
