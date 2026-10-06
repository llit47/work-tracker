"""Exercise the installed management module and wrapper with a real hidden TTY.

All paths are redirected inside a disposable packaging-test installation.
Also runs as the service user in the root-only runtime test.
"""
import fcntl
import os
from pathlib import Path
import pty
import pwd
import select
import sqlite3
import subprocess
import sys
import termios
import time


python, directory, service_user = sys.argv[1:]
directory = Path(directory)
service = pwd.getpwnam(service_user)
data = directory / "identity-data"
data.mkdir(mode=0o750)
if os.geteuid() == 0:
    os.chown(data, service.pw_uid, service.pw_gid)
database = data / "work_tracker.db"
config = directory / "identity.env"
config.write_text(f"DATABASE_URL=sqlite:///{database}\n", encoding="utf-8")
config.chmod(0o640)
if os.geteuid() == 0:
    os.chown(config, 0, service.pw_gid)

# Change constants only in the disposable installed package, never the checkout
# or any production path. There is no production CLI/config override to exploit.
module_path = Path(subprocess.check_output(
    [python, "-I", "-B", "-c", "import app.manage; print(app.manage.__file__)"], text=True,
).strip())
source = module_path.read_text()
source = source.replace('CONFIG_FILE = Path("/etc/work-tracker/work-tracker.env")', f"CONFIG_FILE = Path({str(config)!r})")
source = source.replace('DATABASE_FILE = Path("/var/lib/work-tracker/work_tracker.db")', f"DATABASE_FILE = Path({str(database)!r})")
source = source.replace('SERVICE_USER = "work-tracker"', f"SERVICE_USER = {service_user!r}")
module_path.write_text(source)
wrapper = directory / "create_user.sh"
wrapper_source = (Path(__file__).resolve().parents[1] / "create_user.sh").read_text()
wrapper_source = wrapper_source.replace("/opt/work-tracker/backend/.venv/bin/python", python)
wrapper_source = wrapper_source.replace("runuser -u work-tracker", f"runuser -u {service_user}")
wrapper.write_text(wrapper_source)
wrapper.chmod(0o750)

prefix = ["runuser", "-u", service_user, "--"] if os.geteuid() == 0 else []
# Set up the schema as the service identity, like the normal migration path.
subprocess.run(prefix + [python, "-I", "-B", "-c",
    "from app.manage import DATABASE_FILE; from app.database import build_engine; "
    "from app.models import Base; import os; os.umask(0o027); "
    "Base.metadata.create_all(build_engine(f'sqlite:///{DATABASE_FILE}'))"], check=True)

password = "public-fixture-password-for-tty-test"


def interactive(username, provide_password):
    master, slave = pty.openpty()
    def controlling_terminal():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    process = subprocess.Popen(
        ["bash", str(wrapper), username], stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=controlling_terminal,
        env={**os.environ, "DATABASE_URL": "sqlite:///./wrong.db"},
    )
    os.close(slave)
    output = b""
    sent = 0
    prompts = ["Hasło (ukryte): ".encode(), "Powtórz hasło (ukryte): ".encode()]
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if provide_password and sent < 2 and prompts[sent] in output:
                    os.write(master, (password + "\n").encode())
                    sent += 1
            elif process.poll() is not None:
                break
        result = process.wait(timeout=2)
        assert password.encode() not in output, "Password appeared in terminal output"
        return result, output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


before = database.stat()
result, output = interactive("Owner", True)
assert result == 0, output.decode(errors="replace")
with sqlite3.connect(database) as connection:
    original = connection.execute("SELECT * FROM users").fetchall()
assert len(original) == 1 and original[0][1] == "owner"
assert original[0][2].startswith("$argon2id$")
assert password not in original[0][2]
assert str(database).encode() in output
result, output = interactive("OWNER", False)
assert result == 1 and "już istnieje".encode() in output
assert b"(ukryte)" not in output
with sqlite3.connect(database) as connection:
    assert connection.execute("SELECT * FROM users").fetchall() == original
after = database.stat()
assert (before.st_uid, before.st_gid, before.st_mode) == (after.st_uid, after.st_gid, after.st_mode)
assert not (directory / "wrong.db").exists()
assert not (Path.cwd() / "wrong.db").exists()
print("Installed identity management and hidden-terminal test passed.")
