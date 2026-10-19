#!/usr/bin/env python3
"""Entry point. Loads .env, runs the bot as a child process and restarts it if it crashes.

Serves a small health check on $SERVER_PORT when the host sets one."""
from __future__ import annotations

import asyncio
import glob
import os
import shutil
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _pattern in (str(ROOT / ".local/lib/python*/site-packages"),
                 "/home/container/.local/lib/python*/site-packages"):
    for _site in sorted(glob.glob(_pattern)):
        if _site not in sys.path:
            sys.path.insert(0, _site)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.env import load_env  # noqa: E402

BACKOFF = (1, 2, 5, 10, 30, 60)
HEALTHY_AFTER = 60.0
GRACE = float(os.environ.get("SUPERVISOR_GRACE", "6"))
CHILD_CMD = [sys.executable, "-u", "-m", "pilot"]


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + f" [supervisor] {msg}", flush=True)


def validate_environment() -> None:
    env_file = ROOT / ".env"
    example = ROOT / ".env.example"
    created = False
    if not env_file.exists() and example.is_file():
        try:
            shutil.copyfile(example, env_file)
            if os.name != "nt":
                env_file.chmod(0o600)
            created = True
        except OSError:
            pass
    load_env(env_file)
    if created:
        hint = " (.env was created from .env.example: fill it in and start again)"
    else:
        hint = "" if env_file.exists() else " (no .env found: copy .env.example to .env and fill it in)"
    token = (os.environ.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN_PILOT") or "").strip()
    if not token or ":" not in token:
        raise SystemExit("BOT_TOKEN is missing or invalid. Put the token from @BotFather in .env" + hint)
    owner = os.environ.get("OWNER_ID", "").strip()
    if not owner.isdigit() or int(owner) <= 0:
        raise SystemExit("OWNER_ID is missing. Put your numeric Telegram id in .env (ask @userinfobot)" + hint)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)


class Supervisor:
    def __init__(self, cmd: list[str] | None = None) -> None:
        self.cmd = cmd or CHILD_CMD
        self.stop = asyncio.Event()
        self.child: asyncio.subprocess.Process | None = None
        self.restarts = 0
        self.started_at = time.time()

    async def _pipe(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            print("[pilot] " + line.decode("utf-8", "replace").rstrip(), flush=True)

    async def run_child_forever(self) -> None:
        idx = 0
        env = os.environ.copy()
        env["PYTHONPATH"] = ":".join([str(ROOT), *glob.glob(str(ROOT / ".local/lib/python*/site-packages")),
                                       env.get("PYTHONPATH", "")])
        while not self.stop.is_set():
            t0 = time.monotonic()
            self.child = await asyncio.create_subprocess_exec(
                *self.cmd, cwd=str(ROOT), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            log(f"pilot started pid={self.child.pid}")
            pipe = asyncio.create_task(self._pipe(self.child.stdout))  # type: ignore[arg-type]
            rc = await self.child.wait()
            await pipe
            if self.stop.is_set():
                log(f"pilot exited rc={rc} during shutdown")
                return
            try:
                from shared import update_guard
                msg = update_guard.note_exit(ROOT, time.monotonic() - t0 >= HEALTHY_AFTER)
                if msg:
                    log(msg)
                    idx = 0
            except Exception as exc:  # noqa: BLE001
                log(f"update guard error: {exc!r}")
            if time.monotonic() - t0 >= HEALTHY_AFTER:
                idx = 0
            delay = BACKOFF[min(idx, len(BACKOFF) - 1)]
            idx += 1
            self.restarts += 1
            log(f"pilot exited rc={rc}; restart #{self.restarts} in {delay}s")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        child = self.child
        if child and child.returncode is None:
            log("SIGTERM -> pilot")
            try:
                child.send_signal(signal.SIGTERM)
                await asyncio.wait_for(child.wait(), timeout=GRACE)
            except asyncio.TimeoutError:
                log("pilot did not exit in time -> SIGKILL")
                child.kill()
                await child.wait()
            except ProcessLookupError:
                pass

    async def health(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.read(1024), timeout=2)
            alive = self.child is not None and self.child.returncode is None
            body = (f'{{"bot":"rayzen-pilot","child_alive":{str(alive).lower()},'
                    f'"restarts":{self.restarts},"uptime_s":{int(time.time()-self.started_at)}}}')
            writer.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}").encode())
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()


async def main_async() -> int:
    sup = Supervisor()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(sup.shutdown()))
        except NotImplementedError:
            pass
    server = None
    port = os.environ.get("SERVER_PORT", "").strip()
    if port.isdigit():
        try:
            server = await asyncio.start_server(sup.health, "0.0.0.0", int(port))
            log(f"health endpoint on 0.0.0.0:{port}")
        except OSError as exc:
            log(f"could not bind SERVER_PORT {port}: {exc} (continuing without health endpoint)")
    runner = asyncio.create_task(sup.run_child_forever())
    await sup.stop.wait()
    await sup.shutdown()
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    if server:
        server.close()
    log("supervisor exit")
    return 0


def main() -> int:
    validate_environment()
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
