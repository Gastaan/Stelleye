"""Stelleye on Modal: one always-warm container serving the Django ASGI app (pages, API and the live WebSocket).

    modal deploy modal_app.py        # -> https://<workspace>--stelleye.modal.run

One container on purpose: who is online and the live call channel live in this process's memory
(Channels' in-memory layer), so every visitor must reach the same process.

The database is SQLite on the container's own disk. Modal Volumes have no file locking and commit in
the background, so a live SQLite file there could be snapshotted mid-write; instead a consistent copy
(SQLite's backup API) is saved to the Volume every few seconds after a change and when the container
stops, and restored when one starts.

Setup, once:
    modal volume create stelleye-data
    modal volume put stelleye-data db.sqlite3 /db.sqlite3            # optional: start from an existing database
    modal secret create stelleye-env --from-dotenv server/.env        # contract IDs and testnet keys, plus
                                                                      # DJANGO_SECRET_KEY added to that file
"""
import os
import pathlib
import shutil
import sqlite3
import subprocess
import threading

import modal

SERVER = pathlib.Path(__file__).parent / "server"
WORK = "/tmp/stelleye"                 # container disk: the working database and collected static files
LIVE_DB = f"{WORK}/db.sqlite3"
SNAPSHOT = f"{WORK}/snapshot.sqlite3"
DATA = "/data"                         # the Volume
SAVED_DB = f"{DATA}/db.sqlite3"
SAVE_EVERY_SECONDS = 10

volume = modal.Volume.from_name("stelleye-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_pyproject(str(SERVER / "pyproject.toml"))
    .env({
        "DJANGO_DEBUG": "0",
        "DJANGO_HTTPS": "1",
        "DJANGO_ALLOWED_HOSTS": ".modal.run",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://*.modal.run",
        "DJANGO_DB_PATH": LIVE_DB,
        "DJANGO_STATIC_ROOT": f"{WORK}/static",
        "DEMO_ACCOUNTS": "1",  # testnet demo: the sign-in page offers Maria, Ana, Kofi and the reviewer
    })
    .add_local_dir(SERVER, "/root/server", ignore=[
        ".venv", ".env", "*.sqlite3*", "staticfiles", "media", "e2e", "**/__pycache__",
    ])
)

app = modal.App("stelleye", image=image)


def with_lifespan(application):
    """Channels' router has no 'lifespan' handler; answer it here and pass everything else through."""
    async def asgi(scope, receive, send):
        if scope["type"] != "lifespan":
            return await application(scope, receive, send)
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    return asgi


@app.cls(
    volumes={DATA: volume},
    secrets=[modal.Secret.from_name("stelleye-env")],
    cpu=0.5,
    memory=1024,
    min_containers=1,       # always warm: no cold start for the jury, presence survives quiet spells
    max_containers=1,       # see the module docstring
    timeout=24 * 60 * 60,   # each WebSocket is one call; the default 300 s would cut live connections
)
@modal.concurrent(max_inputs=500)
class Web:
    @modal.enter()
    def start(self):
        os.makedirs(WORK, exist_ok=True)
        if os.path.exists(SAVED_DB):
            shutil.copyfile(SAVED_DB, LIVE_DB)
        for command in (["migrate", "--noinput"], ["collectstatic", "--noinput", "-v", "0"]):
            subprocess.run(["python", "manage.py", *command], cwd="/root/server", check=True)
        self.saved_mtime = os.path.getmtime(LIVE_DB)
        self.stopping = threading.Event()
        threading.Thread(target=self.keep_saving, daemon=True).start()

    def save(self):
        """Copy a consistent snapshot of the database to the Volume, if it changed since the last copy."""
        mtime = os.path.getmtime(LIVE_DB)
        if mtime == self.saved_mtime:
            return
        source, target = sqlite3.connect(LIVE_DB), sqlite3.connect(SNAPSHOT)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        shutil.copyfile(SNAPSHOT, f"{SAVED_DB}.tmp")
        os.replace(f"{SAVED_DB}.tmp", SAVED_DB)
        volume.commit()
        self.saved_mtime = mtime

    def keep_saving(self):
        while not self.stopping.wait(SAVE_EVERY_SECONDS):
            try:
                self.save()
            except Exception as exc:  # keep serving; the next round tries again
                print(f"Saving the database failed: {exc}")

    @modal.exit()
    def stop(self):
        self.stopping.set()
        self.save()

    @modal.asgi_app(label="stelleye")
    def web(self):
        import sys

        sys.path.insert(0, "/root/server")
        os.chdir("/root/server")
        from sightline.asgi import application

        return with_lifespan(application)
