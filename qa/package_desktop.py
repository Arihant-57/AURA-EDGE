"""Package the built dashboard with the tracked desktop and backend source for a prepared laptop."""
import argparse
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = root / "frontend/dist"
    if not (build / "index.html").is_file():
        raise SystemExit("Run the frontend production build first.")
    paths = ["backend/src", "backend/alembic", "backend/alembic.ini", "backend/requirements.txt", "backend/pyproject.toml",
             "frontend/src", "frontend/public", "frontend/index.html", "frontend/package.json", "frontend/package-lock.json",
             "frontend/tsconfig.json", "frontend/vite.config.ts",
             "desktop", "scripts/start-aura-desktop.ps1", "scripts/stop-aura-desktop.ps1",
             "docker-compose.yml", ".env.example", "docs/DESKTOP_USER_GUIDE.md", "docs/THIRD_PARTY.md",
             "docs/JUDGE_DEMO_GUIDE.md", "docs/FINAL_VERIFICATION_REPORT.md", "docs/FINAL_OFFLINE_VERIFICATION.md",
             "docs/FINAL_VOICE_ACCEPTANCE.md", "docs/FINAL_BENCHMARK_REPORT.md", "docs/TESTING_TODO.md",
             "backend/tests", "qa/results/audit-*", "qa/screenshots/independent-*", "qa/screenshots/audit-*",
             "docs/ASR_MODEL_COMPARISON.md", "docs/KNOWN_LIMITATIONS.md", "docs/PROBLEM_STATEMENT_COMPLIANCE.md",
             "docs/CPU_GPU_BENCHMARK_REPORT.md", "docs/VOICE_ACCURACY_REPORT.md", "docs/MULTILINGUAL_TEST_REPORT.md",
             "docs/VOICE_COMMAND_ACCURACY_REPORT.md", "docs/AGENTIC_INTELLIGENCE_REPORT.md",
             "docs/MULTILINGUAL_ACCEPTANCE_REPORT.md", "docs/NOISE_ROBUSTNESS_REPORT.md", "docs/SIMULATOR_GUIDE.md",
             "qa/screenshots/mini-island.png", "qa/screenshots/simulator.png", "qa/results/final-*.json",
             "scripts/test-aura-microphone.ps1", "qa/live_acceptance.py", "qa/loopback_rehearsal.py", "qa/restart_rehearsal.py"]
    listed = subprocess.check_output(["git", "ls-files", "-z", "--", *paths], cwd=root)
    tracked = [root / name.decode("utf8") for name in listed.split(b"\0") if name]
    if (root / "desktop/shutdown_request.py") not in tracked:
        raise SystemExit("The checkout must contain the tracked graceful-shutdown helper.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(tracked + [entry for entry in build.rglob("*") if entry.is_file()]):
            archive.write(path, path.relative_to(root).as_posix())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
