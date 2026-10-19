"""Signed updates for the bot owner.

Trust model: every release has a manifest (JSON) signed with the project's Ed25519 release key. The public key
is pinned in this file (operators of a fork can pin their own with UPDATE_PUBKEY in .env; never through chat).
An archive is accepted only when its size and SHA-256 match the signed manifest entry for THIS edition. A
checksum file published next to the ZIP is integrity metadata, not proof of origin, so it is not used.

Flow (owner only, private chat): check -> download with a size cap -> verify signature, version, edition, hash
and archive layout -> stage under data/updates/staging -> back up the code and the database -> swap the code
entries -> stop polling so app.py restarts the bot -> the new version reports "ok" after it has opened the
database and reached Telegram. If it exits before that twice, shared/update_guard.py moves the old code back.

Never touched: .env, data/ (database, backups), .local/ (host-installed packages). Dependencies are not
installed from chat; if the new requirements are not already satisfied, the owner gets the exact manual step.
Rollback restores code only. Schema migrations are additive, and the pre-update database backup path is
reported so the owner can restore data deliberately if needed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pilot import __version__, authz, confirm, edition, hooks, tg
from pilot.commands import command
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, fmt_dt

log = logging.getLogger("pilot.updater")
ROOT = Path(__file__).resolve().parent.parent
PINNED_KEYS = ("V5rxvn78gNnqWjT4JJ0VjuxPKS81y2lY/frxZK0AuRk=",)       # release_ed25519 (matttsys/RayZen-Pilot)
UPSTREAM = "matttsys/RayZen-Pilot"
MAX_ZIP = 40 * 1024 * 1024
MAX_UNPACKED = 150 * 1024 * 1024
MAX_FILES = 4000
MAX_RATIO = 200
PROTECTED = {"data", ".env", ".local", ".git", "backups", "__pycache__"}
REQUIRED = ("app.py", "pilot/__init__.py", "pilot/_build.py", "requirements.txt")
_lock = asyncio.Lock()


class UpdateError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


ERR_FA = {"no_release": "نسخه‌ای منتشر نشده", "no_manifest": "انتشار امضاشده نیست", "bad_signature": "امضا معتبر نیست",
          "bad_manifest": "مانیفست نامعتبر", "wrong_edition": "نسخهٔ این بیلد در انتشار نیست", "not_newer": "نسخهٔ جدیدتری نیست",
          "too_large": "فایل بیش از حد بزرگ است", "download": "دانلود ناقص یا ناموفق", "hash": "هش فایل با مانیفست نمی‌خواند",
          "bad_archive": "ساختار آرشیو نامعتبر", "unsafe_path": "مسیر ناامن در آرشیو", "deps": "وابستگی‌های جدید نصب نیستند",
          "python": "نسخهٔ پایتون پشتیبانی نمی‌شود", "disk": "فضای دیسک کافی نیست", "busy": "آپدیت دیگری در جریان است",
          "network": "خطای شبکه", "install": "نصب ناموفق؛ فایل‌ها برگردانده شدند", "no_backup": "نسخهٔ پشتیبانی برای بازگشت نیست",
          "no_candidate": "اول بررسی کنید"}
ERR_EN = {"no_release": "no release published", "no_manifest": "release is not signed", "bad_signature": "invalid signature",
          "bad_manifest": "invalid manifest", "wrong_edition": "release has no asset for this edition", "not_newer": "nothing newer",
          "too_large": "file too large", "download": "download failed or incomplete", "hash": "file hash does not match the manifest",
          "bad_archive": "invalid archive layout", "unsafe_path": "unsafe path in archive", "deps": "new dependencies are not installed",
          "python": "unsupported Python version", "disk": "not enough disk space", "busy": "another update is running",
          "network": "network error", "install": "install failed; files were restored", "no_backup": "no backup to roll back to",
          "no_candidate": "run a check first"}


def err_text(e: UpdateError, fa: bool) -> str:
    return (ERR_FA if fa else ERR_EN).get(e.kind, e.kind) + (f" ({esc(e.detail[:120])})" if e.detail else "")


# versions
_VER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse_version(v: str) -> tuple:
    m = _VER.match((v or "").strip())
    if not m:
        raise UpdateError("bad_manifest", f"version {v!r}")
    pre = m.group(4)
    # a pre-release sorts before its release: (5,0,0,0,'alpha.1') < (5,0,0,1,'')
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or ""


def newer(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


# signatures
def public_keys() -> list[bytes]:
    raw = [k.strip() for k in os.environ.get("UPDATE_PUBKEY", "").split(",") if k.strip()] or list(PINNED_KEYS)
    out = []
    for k in raw:
        try:
            b = base64.b64decode(k, validate=True)
        except ValueError:
            continue
        if len(b) == 32:
            out.append(b)
    return out


def verify_signature(manifest: bytes, sig_b64: bytes, keys: list[bytes] | None = None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except ValueError:
        return False
    for k in keys if keys is not None else public_keys():
        try:
            Ed25519PublicKey.from_public_bytes(k).verify(sig, manifest)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def parse_manifest(raw: bytes) -> dict:
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError("bad_manifest", "not JSON")
    if not isinstance(m, dict) or m.get("project") != "rayzen-pilot" or not isinstance(m.get("assets"), dict):
        raise UpdateError("bad_manifest", "fields")
    parse_version(str(m.get("version", "")))
    for ed, a in m["assets"].items():
        if not isinstance(a, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(a.get("sha256", ""))) \
                or not isinstance(a.get("size"), int) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.zip", str(a.get("name", ""))) \
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", str(a.get("root", ""))):
            raise UpdateError("bad_manifest", f"asset {ed}")
    return m


# sources
@dataclass
class Release:
    tag: str
    manifest_raw: bytes = b""
    sig: bytes = b""
    assets: dict = field(default_factory=dict)       # name -> url or local path
    prerelease: bool = False


class GitHubSource:
    """Latest stable release of the configured upstream. Only github.com release-download URLs are fetched."""

    def __init__(self, repo: str | None = None) -> None:
        self.repo = repo or os.environ.get("UPDATE_REPO", "").strip() or UPSTREAM
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,100}", self.repo):
            self.repo = UPSTREAM

    async def _get(self, url: str, limit: int, accept: str = "application/octet-stream") -> bytes:
        import aiohttp
        if os.environ.get("PILOT_OFFLINE"):
            raise UpdateError("network", "offline mode")
        # Ask for the raw bytes. If a server compresses anyway, aiohttp decodes the body and Content-Length then
        # counts the compressed bytes, so the exact-length check only applies to identity responses. The size cap
        # always applies to the decoded bytes, which also bounds a compression bomb.
        headers = {"User-Agent": f"rayzen-pilot/{__version__}", "Accept": accept, "Accept-Encoding": "identity"}
        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=120), allow_redirects=True) as r:
                    if r.status == 404:
                        raise UpdateError("no_release", "404")
                    if r.status != 200:
                        raise UpdateError("network", f"HTTP {r.status}")
                    enc = (r.headers.get("Content-Encoding") or "identity").strip().lower()
                    cl = r.headers.get("Content-Length")
                    if cl and cl.isdigit() and int(cl) > limit:
                        raise UpdateError("too_large", cl)
                    buf = bytearray()
                    async for chunk in r.content.iter_chunked(65536):
                        buf += chunk
                        if len(buf) > limit:
                            raise UpdateError("too_large", str(len(buf)))
                    if enc == "identity" and cl and cl.isdigit() and int(cl) != len(buf):
                        raise UpdateError("download", f"{len(buf)}/{cl} bytes")
                    return bytes(buf)
        except UpdateError:
            raise
        except asyncio.TimeoutError:
            raise UpdateError("network", "timeout")
        except Exception as e:  # noqa: BLE001  (aiohttp.ClientError and friends)
            raise UpdateError("network", type(e).__name__)

    async def latest(self) -> Release:
        raw = await self._get(f"https://api.github.com/repos/{self.repo}/releases/latest", 2_000_000, "application/vnd.github+json")
        try:
            j = json.loads(raw)
        except ValueError:
            raise UpdateError("network", "bad API response")
        prefix = f"https://github.com/{self.repo}/releases/download/"
        assets = {a["name"]: a["browser_download_url"] for a in j.get("assets", [])
                  if isinstance(a, dict) and str(a.get("browser_download_url", "")).startswith(prefix)}
        rel = Release(tag=str(j.get("tag_name", "")), assets=assets, prerelease=bool(j.get("prerelease")))
        man = next((n for n in assets if n.endswith("-manifest.json")), None)
        if not man or man + ".sig" not in assets:
            # The latest stable release can predate signed releases (4.x has no manifest). That is "nothing newer",
            # not a signing problem; an unsigned release that claims to be newer is still refused as unsigned.
            try:
                older = not newer(rel.tag, __version__)
            except UpdateError:
                older = False
            if older:
                raise UpdateError("not_newer", f"{rel.tag} <= {__version__}")
            raise UpdateError("no_manifest", rel.tag)
        rel.manifest_raw = await self._get(assets[man], 200_000)
        rel.sig = await self._get(assets[man + ".sig"], 10_000)
        return rel

    async def fetch(self, rel: Release, name: str, limit: int) -> bytes:
        if name not in rel.assets:
            raise UpdateError("wrong_edition", name)
        return await self._get(rel.assets[name], limit)


class LocalSource:
    """A directory holding manifest, signature and ZIPs: owner uploads (data/updates/inbox) and tests."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def latest(self) -> Release:
        if not self.path.is_dir():
            raise UpdateError("no_release", "inbox empty")
        mans = sorted(p for p in self.path.glob("*-manifest.json") if (self.path / (p.name + ".sig")).is_file())
        if not mans:
            raise UpdateError("no_manifest", "inbox")
        best = None
        for p in mans:
            try:
                v = parse_version(json.loads(p.read_text("utf-8")).get("version", ""))
            except (ValueError, UpdateError, OSError):
                continue
            if best is None or v > best[0]:
                best = (v, p)
        if best is None:
            raise UpdateError("bad_manifest", "inbox")
        p = best[1]
        return Release(tag=p.name, manifest_raw=p.read_bytes(), sig=(self.path / (p.name + ".sig")).read_bytes(),
                       assets={x.name: str(x) for x in self.path.glob("*.zip")})

    async def fetch(self, rel: Release, name: str, limit: int) -> bytes:
        p = rel.assets.get(name)
        if not p or not Path(p).is_file():
            raise UpdateError("wrong_edition", name)
        if Path(p).stat().st_size > limit:
            raise UpdateError("too_large", str(Path(p).stat().st_size))
        return Path(p).read_bytes()


def inbox_dir(root: Path = ROOT) -> Path:
    return root / "data" / "updates" / "inbox"


# archive checks
def _requirements(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*(.*)$", line)
        if m:
            out.append((m.group(1).lower().replace("_", "-"), m.group(3).strip()))
    return out


def _vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def requirement_ok(name: str, spec: str) -> bool:
    """Minimal PEP 440 subset (==, !=, >=, <=, >, <, ~=, trailing .*), enough for our pinned requirements."""
    from importlib import metadata
    try:
        have = _vt(metadata.version(name))
    except metadata.PackageNotFoundError:
        return False
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        m = re.match(r"^(==|>=|<=|!=|~=|>|<)\s*([0-9][0-9A-Za-z.*+-]*)$", part)
        if not m:
            return False
        op, raw = m.groups()
        want = _vt(raw)
        if raw.endswith(".*") and op in ("==", "!="):
            same = have[:len(want)] == want
            if same != (op == "=="):
                return False
            continue
        n = max(len(have), len(want))
        h, w = have + (0,) * (n - len(have)), want + (0,) * (n - len(want))
        if op == "~=":
            ok = h >= w and have[:max(1, len(want) - 1)] == want[:max(1, len(want) - 1)]
        else:
            ok = {"==": h == w, ">=": h >= w, "<=": h <= w, "!=": h != w, ">": h > w, "<": h < w}[op]
        if not ok:
            return False
    return True


def missing_requirements(req_text: str) -> list[str]:
    return [f"{n}{s}" for n, s in _requirements(req_text) if not requirement_ok(n, s)]


def check_archive(data: bytes, root_name: str) -> list[zipfile.ZipInfo]:
    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise UpdateError("bad_archive", str(e)[:80])
    if not infos or len(infos) > MAX_FILES:
        raise UpdateError("bad_archive", f"{len(infos)} entries")
    total = 0
    names = set()
    for i in infos:
        n = i.filename
        if "\\" in n or n.startswith("/") or re.match(r"^[A-Za-z]:", n) or any(part in ("..", "") for part in n.rstrip("/").split("/")):
            raise UpdateError("unsafe_path", n[:80])
        if not (n == root_name + "/" or n.startswith(root_name + "/")):
            raise UpdateError("bad_archive", f"outside {root_name}/: {n[:60]}")
        mode = (i.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise UpdateError("unsafe_path", f"symlink {n[:60]}")
        rel = n[len(root_name) + 1:]
        top = rel.split("/", 1)[0]
        if top in PROTECTED or top.startswith(".env"):
            if top != ".env.example" and rel:
                raise UpdateError("unsafe_path", f"protected {rel[:60]}")
        total += i.file_size
        if total > MAX_UNPACKED:
            raise UpdateError("too_large", "unpacked")
        if i.compress_size and i.file_size / i.compress_size > MAX_RATIO and i.file_size > 1_000_000:
            raise UpdateError("bad_archive", f"compression ratio {n[:60]}")
        names.add(rel)
    for req in REQUIRED:
        if req not in names:
            raise UpdateError("bad_archive", f"missing {req}")
    try:
        bad = zf.testzip()
    except Exception as e:  # noqa: BLE001  (zlib.error, BadZipFile, EOFError ...)
        raise UpdateError("bad_archive", f"crc {type(e).__name__}")
    if bad:
        raise UpdateError("bad_archive", f"crc {bad[:60]}")
    return infos


def extract(data: bytes, root_name: str, dest: Path) -> Path:
    import io
    zf = zipfile.ZipFile(io.BytesIO(data))
    base = dest / root_name
    if dest.exists():
        shutil.rmtree(dest)
    base.mkdir(parents=True)
    for i in zf.infolist():
        rel = i.filename[len(root_name) + 1:]
        if not rel:
            continue
        target = (base / rel).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            raise UpdateError("unsafe_path", rel[:60])
        if i.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(i) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
    return base


def staged_meta(base: Path) -> tuple[str, str]:
    init = (base / "pilot" / "__init__.py").read_text("utf-8")
    build = (base / "pilot" / "_build.py").read_text("utf-8")
    v = re.search(r"__version__\s*=\s*[\"']([^\"']+)", init)
    e = re.search(r"EDITION\s*=\s*[\"']([^\"']+)", build)
    return (v.group(1) if v else ""), (e.group(1) if e else "")


# state
def state_path(root: Path = ROOT) -> Path:
    return root / "data" / "updates" / "state.json"


def read_state(root: Path = ROOT) -> dict:
    from shared import update_guard
    return update_guard.read(root)


def write_state(st: dict, root: Path = ROOT) -> None:
    from shared import update_guard
    update_guard.write(root, st)


@dataclass
class Candidate:
    version: str
    manifest: dict
    staged: Path
    entries: list[str]
    missing: list[str]
    source: str


_candidate: Candidate | None = None


async def check(source=None, root: Path = ROOT, allow_same: bool = False) -> Candidate:
    """Fetch, verify and stage the newest release for this edition. Does not install."""
    global _candidate
    src = source or GitHubSource()
    rel = await src.latest()
    if not verify_signature(rel.manifest_raw, rel.sig):
        raise UpdateError("bad_signature", rel.tag)
    man = parse_manifest(rel.manifest_raw)
    ver = str(man["version"])
    if not allow_same and not newer(ver, __version__):
        raise UpdateError("not_newer", f"{ver} <= {__version__}")
    asset = man["assets"].get(edition.EDITION)
    if not asset:
        raise UpdateError("wrong_edition", edition.EDITION)
    minpy = str(man.get("min_python", "3.10"))
    if sys.version_info[:2] < tuple(int(x) for x in minpy.split(".")[:2]):
        raise UpdateError("python", minpy)
    if asset["size"] > MAX_ZIP:
        raise UpdateError("too_large", str(asset["size"]))
    data = await src.fetch(rel, asset["name"], MAX_ZIP)
    if len(data) != asset["size"]:
        raise UpdateError("download", f"{len(data)}/{asset['size']} bytes")
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise UpdateError("hash", asset["name"])
    infos = check_archive(data, asset["root"])
    unpacked = sum(i.file_size for i in infos)
    if shutil.disk_usage(root).free < unpacked * 3 + 20 * 1024 * 1024:
        raise UpdateError("disk", f"{unpacked // 1048576} MB needed x3")
    staging = root / "data" / "updates" / "staging"
    base = await asyncio.to_thread(extract, data, asset["root"], staging)
    sv, se = staged_meta(base)
    if sv != ver:
        raise UpdateError("bad_archive", f"version {sv} != manifest {ver}")
    if se != edition.EDITION:
        raise UpdateError("wrong_edition", f"archive is {se}")
    entries = sorted(p.name for p in base.iterdir()
                     if p.name == ".env.example" or (p.name not in PROTECTED and not p.name.startswith(".env")))
    missing = missing_requirements((base / "requirements.txt").read_text("utf-8"))
    _candidate = Candidate(ver, man, base, entries, missing, type(src).__name__)
    st = read_state(root)
    st.update({"last_check": time.time(), "last_check_result": f"staged {ver}", "candidate": ver})
    write_state(st, root)
    return _candidate


def _swap(root: Path, staged: Path, entries: list[str], backup: Path) -> None:
    """Move current entries into backup, staged entries into place. Undoes itself on any error."""
    backup.mkdir(parents=True, exist_ok=True)
    done: list[tuple[str, str]] = []           # (op, name)
    try:
        for name in entries:
            cur = root / name
            if cur.exists() or cur.is_symlink():
                os.replace(cur, backup / name)
                done.append(("moved", name))
            os.replace(staged / name, cur)
            done.append(("placed", name))
    except OSError:
        for op, name in reversed(done):
            try:
                if op == "placed":
                    os.replace(root / name, staged / name)
                else:
                    os.replace(backup / name, root / name)
            except OSError:
                log.exception("undo of %s %s failed", op, name)
        raise


async def install(cand: Candidate | None = None, root: Path = ROOT, by: int | None = None, restart=None) -> dict:
    """Back up, swap and restart. Returns the state written to data/updates/state.json."""
    cand = cand or _candidate
    if cand is None or not cand.staged.is_dir():
        raise UpdateError("no_candidate")
    if cand.missing:
        raise UpdateError("deps", ", ".join(cand.missing)[:200])
    if _lock.locked():
        raise UpdateError("busy")
    async with _lock:
        st = read_state(root)
        if st.get("status") in ("restarting", "pending") and time.time() - float(st.get("ts", 0)) < 900:
            raise UpdateError("busy", st.get("status"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = root / "data" / "updates" / f"backup-{__version__}-{stamp}"
        db_backup = None
        if RT.db is not None and RT.db.conn is not None:
            db_backup = await RT.db._call(RT.db._backup, f"pre-update-{cand.version}")
        try:
            await asyncio.to_thread(_swap, root, cand.staged, cand.entries, backup)
        except OSError as e:
            raise UpdateError("install", type(e).__name__)
        st = {"status": "restarting", "from": __version__, "to": cand.version, "edition": edition.EDITION,
              "backup": str(backup.relative_to(root)), "entries": cand.entries, "db_backup": db_backup,
              "by": by, "ts": time.time(), "fails": 0, "notified": False}
        write_state(st, root)
        log.warning("update installed %s -> %s; restarting", __version__, cand.version)
        await (restart or _restart)()
        return st


async def rollback(root: Path = ROOT, by: int | None = None, restart=None) -> dict:
    """Owner-requested rollback to the code saved by the last update."""
    from shared import update_guard
    async with _lock:
        st = read_state(root)
        if not st.get("backup") or not (root / st["backup"]).is_dir():
            raise UpdateError("no_backup")
        ok, msg = await asyncio.to_thread(update_guard.rollback, root, st, f"requested by owner {by or ''}".strip())
        if not ok:
            raise UpdateError("no_backup", msg)
        st = read_state(root)
        st["notified"] = False
        write_state(st, root)
        await (restart or _restart)()
        return st


async def _restart() -> None:
    """Stop polling; main() returns and app.py starts the (new) code."""
    async def later():
        await asyncio.sleep(1.5)
        if RT.app is not None:
            RT.app.stop_running()
    asyncio.get_running_loop().create_task(later())


async def on_started() -> None:
    """Health report after a restart: this point means the database opened and Telegram answered getMe."""
    st = read_state()
    if not st:
        return
    status = st.get("status")
    if status in ("restarting", "pending"):
        if st.get("to") == __version__:
            st.update({"status": "ok", "ok_at": time.time()})
            write_state(st)
            await _notify(f"✅ آپدیت انجام شد: {esc(st.get('from'))} → {esc(__version__)}\n"
                          f"پشتیبان پایگاه داده: <code>{esc(st.get('db_backup') or '-')}</code>")
        else:
            st.update({"status": "failed", "error": f"running {__version__}, expected {st.get('to')}"})
            write_state(st)
            await _notify(f"❌ آپدیت به {esc(st.get('to'))} کامل نشد؛ نسخهٔ فعلی {esc(__version__)} است.")
    elif status == "rolled_back" and not st.get("notified"):
        st["notified"] = True
        write_state(st)
        await _notify(f"↩️ بازگشت به {esc(__version__)} · دلیل: {esc(st.get('reason') or '-')}\n"
                      f"پایگاه داده دست نخورد؛ پشتیبان قبل از آپدیت: <code>{esc(st.get('db_backup') or '-')}</code>")


hooks.ON_STARTED.append(on_started)


async def _notify(text: str) -> None:
    if RT.cfg and RT.bot is not None:
        await tg.send(RT.cfg.owner_id, text)


# policy (rz_meta)
async def policy() -> dict:
    rows = {r["k"]: r["v"] for r in await RT.db.all("SELECT k, v FROM rz_meta WHERE k LIKE 'update_%'")}
    return {"auto": rows.get("update_auto") == "on", "window": rows.get("update_window") or "04:00-05:00"}


async def set_policy(key: str, value: str) -> None:
    await RT.db.run("INSERT OR REPLACE INTO rz_meta(k,v) VALUES(?,?)", ("update_" + key, value))


def in_window(window: str, tz: str, t: float | None = None) -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", window or "")
    if not m:
        return False
    a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    loc = datetime.fromtimestamp(t or time.time(), ZoneInfo(tz))
    x = loc.hour * 60 + loc.minute
    return a <= x < b if a <= b else (x >= a or x < b)


@hooks.periodic("auto_update", 1800)
async def auto_tick() -> None:
    if RT.db is None or RT.cfg is None:
        return
    pol = await policy()
    if not pol["auto"] or not in_window(pol["window"], RT.cfg.tz):
        return
    st = read_state()
    if time.time() - float(st.get("last_auto", 0)) < 20 * 3600:
        return
    st["last_auto"] = time.time()
    write_state(st)
    try:
        cand = await check()
    except UpdateError as e:
        if e.kind not in ("not_newer", "no_release"):
            await _notify("⚠️ آپدیت خودکار: " + err_text(e, True))
        return
    if cand.missing:
        await _notify(f"⚠️ نسخهٔ {esc(cand.version)} آماده است ولی وابستگی‌ها باید دستی نصب شوند:\n"
                      f"<code>{esc(', '.join(cand.missing))}</code>\nسپس /update install")
        return
    await _notify(f"⬆️ آپدیت خودکار به {esc(cand.version)}…")
    try:
        await install(cand, by=0)
    except UpdateError as e:
        await _notify("❌ آپدیت خودکار: " + err_text(e, True))


# owner UI
async def status_line(fa: bool) -> str:
    st = read_state()
    if not st:
        return "بدون سابقه" if fa else "no history"
    s = st.get("status") or "-"
    lab = {"ok": "آخرین آپدیت موفق", "rolled_back": "برگشت خورده", "failed": "ناموفق", "pending": "در حال بررسی",
           "restarting": "در حال راه‌اندازی"}.get(s, s) if fa else s
    return f"{lab} ({esc(st.get('from') or '?')} → {esc(st.get('to') or '?')})" if st.get("to") else esc(lab)


def _result_label(res: str, fa: bool) -> str:
    """last_check_result is an error kind or "staged X.Y.Z"."""
    res = str(res)
    if res.startswith("staged "):
        return ("آماده: " if fa else "staged ") + res[7:]
    return (ERR_FA if fa else ERR_EN).get(res, res)


async def panel_view(fa: bool) -> tuple[str, list]:
    st = read_state()
    pol = await policy()
    tz = RT.cfg.tz if RT.cfg else "UTC"
    root_warn = hasattr(os, "geteuid") and os.geteuid() == 0
    if fa:
        L = ["<b>آپدیت امن</b>", f"نسخهٔ فعلی: {esc(__version__)} · {esc(edition.label('fa'))}",
             f"منبع: <code>{esc(GitHubSource().repo)}</code> (فقط انتشار امضاشده)",
             f"وضعیت: {await status_line(True)}",
             f"آخرین بررسی: {fmt_dt(st['last_check'], tz, 'fa') if st.get('last_check') else '—'}"
             + (f" · {esc(_result_label(st['last_check_result'], True))}" if st.get("last_check_result") else ""),
             f"آپدیت خودکار: {'روشن' if pol['auto'] else 'خاموش'} · بازه {esc(pol['window'])}"]
        if _candidate:
            L.append(f"آمادهٔ نصب: {esc(_candidate.version)}" + (f" · ⚠️ وابستگی: {esc(', '.join(_candidate.missing))}" if _candidate.missing else ""))
        if root_warn:
            L.append("⚠️ ربات با کاربر root اجرا می‌شود؛ توصیه نمی‌شود.")
    else:
        L = ["<b>Secure updates</b>", f"Current: {esc(__version__)} · {esc(edition.label('en'))}",
             f"Source: <code>{esc(GitHubSource().repo)}</code> (signed releases only)", f"State: {await status_line(False)}",
             f"Last check: {fmt_dt(st['last_check'], tz, 'en') if st.get('last_check') else '—'}"
             + (f" · {esc(_result_label(st['last_check_result'], False))}" if st.get("last_check_result") else ""),
             f"Auto-update: {'on' if pol['auto'] else 'off'} · window {esc(pol['window'])}"]
        if _candidate:
            L.append(f"Ready to install: {esc(_candidate.version)}" + (f" · ⚠️ deps: {esc(', '.join(_candidate.missing))}" if _candidate.missing else ""))
        if root_warn:
            L.append("⚠️ Running as root is not recommended.")
    kb = [[("🔎 " + ("بررسی" if fa else "Check"), "op:u_check:0"), ("📥 " + ("از فایل‌های ارسالی" if fa else "From uploads"), "op:u_inbox:0")]]
    if _candidate and not _candidate.missing:
        kb.append([("⬆️ " + (f"نصب {_candidate.version}" if fa else f"Install {_candidate.version}"), "op:u_install:0")])
    if st.get("backup") and (ROOT / st["backup"]).is_dir() and st.get("status") in ("ok", "failed"):
        kb.append([("↩️ " + (f"بازگشت به {st.get('from')}" if fa else f"Roll back to {st.get('from')}"), "op:u_rollback:0")])
    kb.append([(("خاموش کردن آپدیت خودکار" if pol["auto"] else "روشن کردن آپدیت خودکار") if fa else
                ("Turn auto-update off" if pol["auto"] else "Turn auto-update on"), "op:u_auto:0")])
    return "\n".join(L), kb


async def _do_check(fa: bool, source=None) -> str:
    try:
        cand = await check(source)
    except UpdateError as e:
        st = read_state()
        st.update({"last_check": time.time(), "last_check_result": e.kind})
        write_state(st)
        return ("نتیجه: " if fa else "Result: ") + err_text(e, fa)
    if cand.missing:
        return ((f"نسخهٔ {esc(cand.version)} تأیید شد ولی این وابستگی‌ها نصب نیستند:\n<code>{esc(', '.join(cand.missing))}</code>\n"
                 "روی سرور: <code>pip install -r requirements.txt</code> (از پوشهٔ آماده‌شده) و دوباره بررسی کنید.") if fa else
                (f"{esc(cand.version)} verified, but these dependencies are missing:\n<code>{esc(', '.join(cand.missing))}</code>\n"
                 "On the server run <code>pip install -r requirements.txt</code>, then check again."))
    return (f"✅ نسخهٔ {esc(cand.version)} امضا و هش تأیید شد و آمادهٔ نصب است." if fa else
            f"✅ {esc(cand.version)} signature and hash verified; ready to install.")


async def on_panel_action(q, c: Cmd, view: str, arg: str) -> None:
    from pilot import owner_panel
    fa = c.lang == "fa"
    if not owner_panel.allowed(q.from_user.id, c.chat_id):          # second check; the panel checks first
        return await tg.answer(q, "⛔️", alert=True)
    if view in ("u_check", "u_inbox"):
        await tg.answer(q, "در حال بررسی…" if fa else "Checking…")
        msg = await _do_check(fa, LocalSource(inbox_dir()) if view == "u_inbox" else None)
        await c.say(msg, reply=False)
    elif view == "u_install":
        if not _candidate:
            return await tg.answer(q, err_text(UpdateError("no_candidate"), fa), alert=True)
        tok = await confirm.create(c.chat_id, q.from_user.id, "upd_install", {"version": _candidate.version}, ttl=300)
        await tg.answer(q)
        await c.say((f"نصب {esc(_candidate.version)}؟ ربات چند ثانیه قطع می‌شود. .env و داده‌ها دست نمی‌خورند و قبلش پشتیبان گرفته می‌شود."
                     if fa else f"Install {esc(_candidate.version)}? The bot restarts for a few seconds. .env and data are kept and backed up first."),
                    rows=confirm.buttons(tok, c.lang), reply=False)
    elif view == "u_rollback":
        st = read_state()
        tok = await confirm.create(c.chat_id, q.from_user.id, "upd_rollback", {"to": st.get("from")}, ttl=300)
        await tg.answer(q)
        await c.say((f"بازگشت کد به {esc(st.get('from'))}؟ پایگاه داده بازگردانده نمی‌شود (پشتیبان: <code>{esc(st.get('db_backup') or '-')}</code>)."
                     if fa else f"Roll code back to {esc(st.get('from'))}? The database is not restored (backup: <code>{esc(st.get('db_backup') or '-')}</code>)."),
                    rows=confirm.buttons(tok, c.lang), reply=False)
    elif view == "u_auto":
        pol = await policy()
        tok = await confirm.create(c.chat_id, q.from_user.id, "upd_auto", {"on": not pol["auto"]}, ttl=300)
        await tg.answer(q)
        await c.say((("آپدیت خودکار روشن شود؟ فقط انتشارهای پایدار امضاشده، در بازهٔ " + esc(pol["window"]) + " و بدون وابستگی تازه.")
                     if not pol["auto"] else "آپدیت خودکار خاموش شود؟") if fa else
                    (("Turn on auto-update? Signed stable releases only, during " + esc(pol["window"]) + ", without new dependencies.")
                     if not pol["auto"] else "Turn auto-update off?"), rows=confirm.buttons(tok, c.lang), reply=False)
    else:
        await tg.answer(q)
    txt, kb = await panel_view(fa)
    await tg.edit(q.message, txt, kb + owner_panel.nav_rows("update", fa))


def _owner_only(presser: int, chat_id: int) -> bool:
    return authz.is_owner(presser) and chat_id == presser


@confirm.handler("upd_install")
async def _install_ok(p: dict, presser: int, chat_id: int, message) -> str:
    if not _owner_only(presser, chat_id):
        return "⛔️"
    if not _candidate or _candidate.version != p.get("version"):
        return "منقضی شده؛ دوباره بررسی کنید."
    try:
        await install(_candidate, by=presser)
    except UpdateError as e:
        return "❌ " + err_text(e, True)
    return f"⬆️ نصب شد؛ راه‌اندازی دوباره با {esc(p.get('version'))}…"


@confirm.handler("upd_rollback")
async def _rollback_ok(p: dict, presser: int, chat_id: int, message) -> str:
    if not _owner_only(presser, chat_id):
        return "⛔️"
    try:
        await rollback(by=presser)
    except UpdateError as e:
        return "❌ " + err_text(e, True)
    return "↩️ برگشت؛ راه‌اندازی دوباره…"


@confirm.handler("upd_auto")
async def _auto_ok(p: dict, presser: int, chat_id: int, message) -> str:
    if not _owner_only(presser, chat_id):
        return "⛔️"
    await set_policy("auto", "on" if p.get("on") else "off")
    return "✅ آپدیت خودکار " + ("روشن شد." if p.get("on") else "خاموش شد.")


@command("update", "upgrade", scope="private", admin=False, section="owner")
async def k_update(c: Cmd):
    from pilot import owner_panel
    if not owner_panel.allowed(c.actor_id, c.chat_id):
        return await c.say(c.t("این بخش فقط برای مالک ربات است.", "Bot owner only."))
    fa = c.lang == "fa"
    sub = (c.args[0].lower() if c.args else "")
    if sub in ("check", "بررسی"):
        return await c.say(await _do_check(fa))
    if sub in ("inbox", "file", "فایل"):
        return await c.say(await _do_check(fa, LocalSource(inbox_dir())))
    if sub in ("window", "بازه") and len(c.args) > 1:
        w = c.args[1].replace("–", "-")
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d", w):
            return await c.say("<code>/update window 04:00-05:00</code>")
        await set_policy("window", w)
        return await c.say(c.t(f"✅ بازهٔ آپدیت خودکار: {esc(w)}", f"✅ Auto-update window: {esc(w)}"))
    txt, kb = await panel_view(fa)
    await c.say(txt, rows=kb + owner_panel.nav_rows("update", fa))


async def on_private_document(m) -> bool:
    """Owner uploads release files (manifest, .sig, .zip) to the bot's private chat for an offline update."""
    d = m.document
    if d is None or not authz.is_owner(m.from_user.id if m.from_user else None):
        return False
    name = d.file_name or ""
    if not re.fullmatch(r"rayzen-pilot-[A-Za-z0-9._-]{1,100}(\.zip|-manifest\.json|-manifest\.json\.sig)", name):
        return False
    if (d.file_size or 0) > min(MAX_ZIP, 20 * 1024 * 1024):
        await tg.send(m.chat.id, "❌ فایل بزرگ‌تر از حد مجاز ربات‌ها برای دریافت است.")
        return True
    f = await tg.call(RT.bot.get_file, file_id=d.file_id)
    if not f.ok:
        await tg.send(m.chat.id, "❌ دریافت فایل نشد: " + esc(f.desc))
        return True
    box = inbox_dir()
    box.mkdir(parents=True, exist_ok=True)
    buf = await f.value.download_as_bytearray()
    (box / name).write_bytes(bytes(buf))
    await tg.send(m.chat.id, f"📥 ذخیره شد: <code>{esc(name)}</code>\nوقتی مانیفست، امضا و ZIP رسید: <code>/update inbox</code>")
    return True


hooks.PRIVATE_DOCUMENT.append(on_private_document)
