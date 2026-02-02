from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote


def _configure_logging() -> None:
	"""Configure minimal logging.

	Enable debug logs by setting COLLECT_ARTIFACTS_LOG=DEBUG (or INFO/WARNING/etc).
	"""
	level_name = (os.environ.get("COLLECT_ARTIFACTS_LOG") or "INFO").strip().upper()
	level = getattr(logging, level_name, logging.INFO)
	logging.basicConfig(level=level, format="[collect_artifacts] %(levelname)s: %(message)s")


def _fmt_path(p: Path) -> str:
	# Keep log output stable across machines.
	return str(p.resolve()).replace("\\", "/")


@dataclass(frozen=True)
class SessionTiming:
	start_ms: int
	end_ms: int

	def start_iso9075(self) -> str:
		return datetime.fromtimestamp(self.start_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

	def end_iso9075(self) -> str:
		return datetime.fromtimestamp(self.end_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _find_repo_root(start: Path) -> Path:
	"""Infer repo root from where the command is run.

	We prefer the nearest parent directory that contains either:
	- final_prompt.txt (required input)
	- .env (optional config)

	This makes it possible to run this script from any location.
	"""
	start = start.resolve()
	for candidate in (start, *start.parents):
		if (candidate / "final_prompt.txt").exists() or (candidate / ".env").exists():
			return candidate
	return start


def _load_dotenv(dotenv_path: Path) -> dict[str, str]:
	"""Minimal .env parser (KEY=VALUE, supports quotes, ignores comments)."""
	if not dotenv_path.exists():
		return {}
	out: dict[str, str] = {}
	for raw in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if "=" not in line:
			continue
		k, v = line.split("=", 1)
		k = k.strip()
		v = v.strip()
		if not k:
			continue
		if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
			v = v[1:-1]
		out[k] = v
	return out


def _apply_dotenv(dotenv: dict[str, str]) -> None:
	# Override environment with .env values to make behavior reproducible.
	for k, v in dotenv.items():
		os.environ[k] = v


def _split_csv(value: str) -> list[str]:
	return [p.strip() for p in value.split(",") if p.strip()]


def _workspace_uri_for_folder(folder: Path) -> str:
	"""Match VS Code workspace.json folder format: file:///c%3A/..."""

	resolved = folder.resolve()
	drive = resolved.drive  # e.g. 'C:'
	if not drive or not drive.endswith(":"):
		raise ValueError(f"Unexpected drive in path: {resolved}")

	drive_letter = drive[0].lower()
	# Strip 'C:' then URL-encode, keeping slashes
	path_no_drive = str(resolved)[2:].replace("\\", "/")
	path_no_drive = quote(path_no_drive, safe="/")
	return f"file:///{drive_letter}%3A{path_no_drive}"


def _as_forward_slash(p: Path) -> str:
	return str(p).replace("\\", "/")


def _relativize_text(text: str, *, model_root: Path, repo_root: Path) -> str:
	"""Rewrite absolute paths/URIs to be relative (model_root preferred, then repo_root)."""

	model_abs = model_root.resolve()
	repo_abs = repo_root.resolve()
	model_forward = _as_forward_slash(model_abs)
	repo_forward = _as_forward_slash(repo_abs)

	# Replace plain absolute paths that appear verbatim
	for base, label in [(model_abs, ""), (repo_abs, "")]:
		b1 = str(base)
		b2 = _as_forward_slash(base)
		# Normalize trailing separator
		for b in {b1.rstrip("\\/"), b2.rstrip("\\/")}:  # type: ignore[arg-type]
			if not b:
				continue
			text = text.replace(b + "\\", "")
			text = text.replace(b + "/", "")

	# Rewrite file:// URIs inside parentheses: (...file:///c%3A/Users/...)
	def repl_uri(match: re.Match[str]) -> str:
		uri = match.group(1)
		decoded = unquote(uri)
		# decoded looks like file:///c:/Users/... or file:///c:/...
		path_part = decoded
		if decoded.startswith("file:///"):
			path_part = decoded[len("file:///"):]
		# Windows drive form: c:/...
		# Keep as forward slashes for stable output.
		path_part = path_part.replace("\\", "/")
		# If this URI points inside model or repo, shorten.
		if path_part.lower().startswith(model_forward.lower().lstrip("/")):
			rel = path_part[len(model_forward.lstrip("/")):].lstrip("/")
			return f"({rel})"
		if path_part.lower().startswith(repo_forward.lower().lstrip("/")):
			rel = path_part[len(repo_forward.lstrip("/")):].lstrip("/")
			return f"({rel})"
		return match.group(0)

	text = re.sub(r"\((file:///[^)]+)\)", repl_uri, text)
	return text


def _read_text_best_effort(path: Path) -> str:
	return path.read_text(encoding="utf-8", errors="ignore")


def find_workspace_storage_dir(storage_root: Path, workspace_uri: str) -> Optional[Path]:
	if not storage_root.exists():
		return None
	for workspace_json in storage_root.glob("*/workspace.json"):
		try:
			raw = _read_text_best_effort(workspace_json)
		except Exception:
			continue
		if workspace_uri in raw:
			return workspace_json.parent
	return None


def _sqlite_get_value(db_path: Path, key: str) -> Optional[str]:
	if not db_path.exists():
		return None
	con = sqlite3.connect(str(db_path))
	try:
		cur = con.cursor()
		cur.execute("SELECT value FROM ItemTable WHERE key=?", (key,))
		row = cur.fetchone()
		if not row:
			return None
		return row[0]
	finally:
		con.close()


def extract_session_timing(db_path: Path) -> Optional[SessionTiming]:
	raw = _sqlite_get_value(db_path, "chat.ChatSessionStore.index")
	if not raw:
		return None
	try:
		payload = json.loads(raw)
	except Exception:
		return None

	entries = (payload.get("entries") or {}).values()
	starts: list[int] = []
	ends: list[int] = []
	for entry in entries:
		timing = (entry or {}).get("timing") or {}

		# VS Code stable (and some builds) use startTime/endTime
		start = timing.get("startTime")
		end = timing.get("endTime")
		if isinstance(start, int) and isinstance(end, int):
			starts.append(start)
			ends.append(end)
			continue

		# VS Code Insiders can use created/lastRequestEnded
		created = timing.get("created")
		last_ended = timing.get("lastRequestEnded")
		if isinstance(created, int) and isinstance(last_ended, int):
			starts.append(created)
			ends.append(last_ended)
			continue

		# Best-effort fallback
		created = timing.get("created")
		last_msg = (entry or {}).get("lastMessageDate")
		if isinstance(created, int) and isinstance(last_msg, int):
			starts.append(created)
			ends.append(last_msg)

	if not starts or not ends:
		return None
	return SessionTiming(start_ms=min(starts), end_ms=max(ends))


def extract_message_window_timing(chat_sessions_dir: Path) -> Optional[SessionTiming]:
	"""Compute timing from first user message sent to last model response completed.

	This differs from the VS Code session timing index (which can include idle time).
	
	We use:
	- request['timestamp'] (epoch ms) as the user-send moment
	- request['result']['timings']['totalElapsed'] (ms duration) to estimate completion
	  => end = timestamp + totalElapsed
	Fallbacks:
	- request['timeSpentWaiting'] (ms duration)
	- request['timestamp'] only
	"""
	if not chat_sessions_dir.exists():
		return None

	session_files = sorted(
		[*chat_sessions_dir.glob("*.json"), *chat_sessions_dir.glob("*.jsonl")],
		key=lambda p: p.stat().st_mtime,
	)
	if not session_files:
		return None

	starts: list[int] = []
	ends: list[int] = []

	for session_file in session_files:
		session = _load_chat_session_file(session_file)
		if not isinstance(session, dict):
			continue
		requests = session.get("requests")
		if not isinstance(requests, list):
			continue

		for req in requests:
			if not isinstance(req, dict):
				continue
			ts = req.get("timestamp")
			if not isinstance(ts, int):
				continue
			starts.append(ts)

			end_ts = ts
			result = req.get("result")
			if isinstance(result, dict):
				timings = result.get("timings")
				if isinstance(timings, dict):
					total = timings.get("totalElapsed")
					if isinstance(total, int) and total >= 0:
						end_ts = ts + total
			waiting = req.get("timeSpentWaiting")
			# Some builds store an epoch ms here (bug/format difference). Only
			# treat it as a duration when it looks like a small interval.
			if isinstance(waiting, int) and 0 <= waiting <= 86_400_000:
				end_ts = max(end_ts, ts + waiting)
			ends.append(end_ts)

	if not starts or not ends:
		return None
	return SessionTiming(start_ms=min(starts), end_ms=max(ends))


def _set_path(root: Any, path: list[Any], value: Any) -> None:
	cur = root
	for key in path[:-1]:
		if isinstance(key, int):
			if not isinstance(cur, list) or key < 0 or key >= len(cur):
				return
			cur = cur[key]
		else:
			if not isinstance(cur, dict) or key not in cur:
				return
			cur = cur[key]

	last = path[-1]
	if isinstance(last, int):
		if isinstance(cur, list) and 0 <= last < len(cur):
			cur[last] = value
	else:
		if isinstance(cur, dict):
			cur[last] = value


def _get_path(root: Any, path: list[Any]) -> Any:
	cur = root
	for key in path:
		if isinstance(key, int):
			if not isinstance(cur, list) or key < 0 or key >= len(cur):
				return None
			cur = cur[key]
		else:
			if not isinstance(cur, dict) or key not in cur:
				return None
			cur = cur[key]
	return cur


def _insert_path(root: Any, path: list[Any], index: int, values: list[Any]) -> None:
	target = _get_path(root, path)
	if not isinstance(target, list):
		return
	if index < 0:
		index = 0
	if index > len(target):
		index = len(target)
	target[index:index] = values


def _load_chat_session_file(path: Path) -> Optional[dict[str, Any]]:
	if path.suffix.lower() == ".json":
		try:
			return json.loads(_read_text_best_effort(path))
		except Exception:
			return None

	if path.suffix.lower() == ".jsonl":
		session: Optional[dict[str, Any]] = None
		try:
			with path.open("r", encoding="utf-8", errors="ignore") as f:
				for raw in f:
					s = raw.strip()
					if not s:
						continue
					try:
						evt = json.loads(s)
					except Exception:
						continue

					kind = evt.get("kind")
					if kind == 0:
						v = evt.get("v")
						if isinstance(v, dict):
							session = v
					elif session is not None and kind == 1:
						k = evt.get("k")
						if isinstance(k, list):
							_set_path(session, k, evt.get("v"))
					elif session is not None and kind == 2:
						k = evt.get("k")
						if not isinstance(k, list):
							continue
						v = evt.get("v")
						if "i" in evt:
							i = evt.get("i")
							if isinstance(i, int) and isinstance(v, list):
								_insert_path(session, k, i, v)
							elif isinstance(i, int):
								_insert_path(session, k, i, [v])
						else:
							_set_path(session, k, v)
		except Exception:
			return None
		return session

	return None


def _extract_user_text(message: Any) -> str:
	if isinstance(message, dict):
		text = message.get("text")
		if isinstance(text, str):
			return text.strip()
		parts = message.get("parts")
		if isinstance(parts, list):
			chunks: list[str] = []
			for p in parts:
				if isinstance(p, dict) and isinstance(p.get("text"), str):
					chunks.append(p["text"])
				elif isinstance(p, str):
					chunks.append(p)
			return "".join(chunks).strip()
	if isinstance(message, str):
		return message.strip()
	return ""


def _extract_assistant_text(response: Any) -> str:
	if isinstance(response, str):
		return response.strip()
	if isinstance(response, dict):
		val = response.get("value")
		if isinstance(val, str):
			return val.strip()
	if isinstance(response, list):
		chunks: list[str] = []
		for part in response:
			if not isinstance(part, dict):
				continue

			kind = part.get("kind")

			# Plain assistant-visible markdown/text chunks
			if kind is None:
				val = part.get("value")
				if isinstance(val, str):
					txt = val.strip()
					if txt and (not chunks or chunks[-1] != txt):
						chunks.append(txt)
				continue

			# Tool/action chunks (these are what your compare.txt contains)
			if kind == "toolInvocationSerialized":
				# Prefer the already human-friendly past tense message when available
				pt = part.get("pastTenseMessage")
				if isinstance(pt, dict) and isinstance(pt.get("value"), str) and pt.get("value").strip():
					line = pt["value"].strip()
					if not chunks or chunks[-1] != line:
						chunks.append(line)
					continue

				# Some tools store structured input/output in resultDetails
				result_details = part.get("resultDetails")
				invocation = part.get("invocationMessage")
				if isinstance(invocation, str) and invocation.strip():
					line = invocation.strip()
					if not chunks or chunks[-1] != line:
						chunks.append(line)

				if isinstance(result_details, dict):
					inp = result_details.get("input")
					if isinstance(inp, str) and inp.strip():
						line = f"Completed with input: {inp.strip()}"
						if not chunks or chunks[-1] != line:
							chunks.append(line)
					out = result_details.get("output")
					# Keep output compact; it's often long.
					if isinstance(out, list) and out:
						first = out[0]
						if isinstance(first, dict) and isinstance(first.get("value"), str) and first.get("value").strip():
							text = first["value"].strip().splitlines()
							header = text[0] if text else ""
							if header:
								line = header
								if not chunks or chunks[-1] != line:
									chunks.append(line)
					continue

				# Terminal runs store the command line in toolSpecificData
				if part.get("toolId") == "run_in_terminal":
					tsd = part.get("toolSpecificData")
					if isinstance(tsd, dict) and isinstance(tsd.get("commandLine"), str) and tsd.get("commandLine").strip():
						line = f"Ran terminal command: {tsd['commandLine'].strip()}"
						if not chunks or chunks[-1] != line:
							chunks.append(line)
						continue

				# Fallback
				if isinstance(invocation, str) and invocation.strip():
					line = invocation.strip()
					if not chunks or chunks[-1] != line:
						chunks.append(line)
				continue
		return "\n".join(chunks).strip()
	return ""


def export_transcript(chat_sessions_dir: Path) -> Optional[str]:
	# repo_root and model_root are inferred from chat_sessions_dir path
	# (caller passes them explicitly below)
	if not chat_sessions_dir.exists():
		return None

	session_files = sorted(
		[*chat_sessions_dir.glob("*.json"), *chat_sessions_dir.glob("*.jsonl")],
		key=lambda p: p.stat().st_mtime,
	)
	if not session_files:
		return None

	out_chunks: list[str] = []
	for session_file in session_files:
		session = _load_chat_session_file(session_file)
		if not isinstance(session, dict):
			continue

		title = session.get("customTitle") or ""
		if isinstance(title, str) and title.strip():
			out_chunks.append(f"Session: {title.strip()}")

		requests = session.get("requests")
		if not isinstance(requests, list):
			continue

		for req in requests:
			if not isinstance(req, dict):
				continue
			user_text = _extract_user_text(req.get("message"))
			assistant_text = _extract_assistant_text(req.get("response"))

			if user_text:
				out_chunks.append("User: " + user_text)
			if assistant_text:
				out_chunks.append("GitHub Copilot: " + assistant_text)
			out_chunks.append("")

		out_chunks.append("---")
		out_chunks.append("")

	transcript = "\n".join(out_chunks).rstrip() + "\n"
	if transcript.strip("\n- ") == "":
		return None
	return transcript


def main() -> int:
	_configure_logging()

	repo_root = _find_repo_root(Path.cwd())
	logging.info("cwd=%s", _fmt_path(Path.cwd()))
	logging.info("repo_root=%s", _fmt_path(repo_root))

	_apply_dotenv(_load_dotenv(repo_root / ".env"))

	# Optional: copy PR description/prompt file into each model folder if missing.
	# File name is configured via .env: PR_DESCRIPTION_FILE (defaults to final_prompt.txt).
	prompt_filename = os.environ.get("PR_DESCRIPTION_FILE") or "final_prompt.txt"
	logging.info("prompt_filename=%s", prompt_filename)

	model_dirs = [
		d
		for d in repo_root.iterdir()
		if d.is_dir() and (d / "pyproject.toml").exists() and d.name != "main"
	]
	logging.info("found_model_dirs=%d", len(model_dirs))
	if logging.getLogger().isEnabledFor(logging.DEBUG):
		for d in sorted(model_dirs, key=lambda p: p.name.lower()):
			logging.debug("model_dir=%s", _fmt_path(d))

	final_prompt_src = repo_root / prompt_filename
	has_final_prompt_src = final_prompt_src.exists() and final_prompt_src.is_file()
	logging.info("final_prompt_src=%s exists=%s", _fmt_path(final_prompt_src), has_final_prompt_src)

	appdata = os.environ.get("APPDATA")
	if not appdata:
		raise SystemExit("APPDATA is not set; set it or provide it in .env")
	appdata_path = Path(appdata)
	variants = _split_csv(os.environ.get("VSCODE_VARIANTS", "Code,Code - Insiders"))
	storage_roots = [appdata_path / v / "User" / "workspaceStorage" for v in variants]
	logging.info("vscode_variants=%s", ",".join(variants) if variants else "")
	for sr in storage_roots:
		logging.debug("workspaceStorage_root=%s exists=%s", _fmt_path(sr), sr.exists())

	timings: dict[str, SessionTiming] = {}
	wrote_transcripts = 0
	touched_empty = 0
	ws_found = 0
	ws_missing = 0

	for model_dir in sorted(model_dirs, key=lambda p: p.name.lower()):
		model_name = model_dir.name
		logging.info("processing_model=%s", model_name)

		# Task 3
		final_prompt_dst = model_dir / prompt_filename
		if has_final_prompt_src and not final_prompt_dst.exists():
			final_prompt_dst.write_text(final_prompt_src.read_text(encoding="utf-8"), encoding="utf-8")
			logging.debug("copied_prompt_to=%s", _fmt_path(final_prompt_dst))

		workspace_uri = _workspace_uri_for_folder(model_dir)
		ws_dir: Optional[Path] = None
		for storage_root in storage_roots:
			candidate = find_workspace_storage_dir(storage_root, workspace_uri)
			if candidate:
				ws_dir = candidate
				break
		if ws_dir:
			ws_found += 1
			logging.debug("workspaceStorage_match=%s", _fmt_path(ws_dir))
		else:
			ws_missing += 1
			logging.warning(
				"no_workspaceStorage_match for model=%s (uri=%s)",
				model_name,
				workspace_uri,
			)

		transcript: Optional[str] = None
		timing: Optional[SessionTiming] = None

		if ws_dir:
			transcript = export_transcript(ws_dir / "chatSessions")
			if transcript:
				transcript = _relativize_text(transcript, model_root=model_dir, repo_root=repo_root)
			timing = extract_message_window_timing(ws_dir / "chatSessions")
			if timing is None:
				# Fallback: VS Code session index timing (can include idle gaps)
				timing = extract_session_timing(ws_dir / "state.vscdb")

		# Task 1
		model_txt = model_dir / f"{model_name}.txt"
		if transcript:
			model_txt.write_text(transcript, encoding="utf-8")
			wrote_transcripts += 1
			logging.info("wrote_transcript=%s bytes=%d", _fmt_path(model_txt), len(transcript.encode("utf-8")))
		else:
			model_txt.touch(exist_ok=True)
			touched_empty += 1
			logging.warning("empty_transcript_touched=%s", _fmt_path(model_txt))

		if timing:
			timings[model_name] = timing

	# Task 2
	time_lines: list[str] = []
	for model_name in sorted(timings.keys(), key=lambda s: s.lower()):
		t = timings[model_name]
		time_lines.append(f"{model_name}:{t.start_iso9075()},{t.end_iso9075()}")
	(repo_root / "time.txt").write_text("\n".join(time_lines) + ("\n" if time_lines else ""), encoding="utf-8")
	logging.info(
		"wrote_time=%s lines=%d (ws_found=%d ws_missing=%d transcripts=%d empty=%d)",
		_fmt_path(repo_root / "time.txt"),
		len(time_lines),
		ws_found,
		ws_missing,
		wrote_transcripts,
		touched_empty,
	)

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
