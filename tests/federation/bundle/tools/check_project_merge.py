"""Reproduce a semantic integration error on two Git branches in a temporary repo."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def check(directory):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=Federation fixture", "-c",
             "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
            cwd=directory, check=True, capture_output=True, text=True)

    def save(name, content):
        (directory / name).write_text(content, encoding="utf-8")

    def commit(message):
        git("add", ".")
        git("commit", "-qm", message)

    def expect(label, code, value):
        run = subprocess.run([sys.executable, "-B", "example.py"], cwd=directory,
                             capture_output=True, text=True)
        if (run.returncode, run.stdout.strip()) != (code, value):
            raise RuntimeError(f"{label}: {run.returncode}, {run.stdout}, {run.stderr}")
        print(f"{label}: {value}")

    git("init", "-q", "-b", "main")
    save("exporter.py", 'def record():\n    return {"amount": 12, "unit": "dollars"}\n')
    save("screen.py", 'def dollars(r):\n    return r["amount"] / (100 if r["unit"] == "cents" else 1)\n')
    save("example.py", 'from exporter import record\nfrom screen import dollars\n'
         'value = dollars(record())\nprint(f"{value:.2f}")\nraise SystemExit(0 if value == 12 else 1)\n')
    commit("Working units-aware baseline")
    git("switch", "-qc", "cents-export")
    save("exporter.py", 'def record():\n    return {"amount": 1200, "unit": "cents"}\n')
    commit("Export cents with a unit tag")
    expect("exporter branch", 0, "12.00")
    git("switch", "-qc", "screen-redesign", "main")
    save("screen.py", 'def dollars(r):\n    return r["amount"]\n')
    commit("Screen designed against the old contract")
    expect("screen branch", 0, "12.00")
    git("switch", "-qc", "integration", "cents-export")
    git("merge", "--no-ff", "-qm", "Trial integration", "screen-redesign")
    expect("combined revision correctly fails", 1, "1200.00")
    save("screen.py", 'def dollars(r):\n    return r["amount"] / {"cents": 100, "dollars": 1}[r["unit"]]\n')
    commit("Honor declared unit")
    expect("integrated correction", 0, "12.00")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="asimov-project-") as temp:
        check(Path(temp))
