"""Export only source changes from the two pinned upstream clones. Never exports credentials."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXTRA = {
    "cyber-xiaoya-live": ["README.local.md", "requirements.macos.lock.txt", "src/local_main.py",
        "src/local_support.py", "src/tts/macos.py", "local_tests/test_macos_integration.py", "local_tests/viewer.cjs"],
    "cyber-xiaoya-danmaku": [],
}

def main():
    folder = ROOT / "vendor-patches"
    folder.mkdir(exist_ok=True)
    manifest = []
    for name, extra in EXTRA.items():
        repo = ROOT / "upstream" / name
        def git(*args): return subprocess.check_output(["git", *args], cwd=repo)
        sha = git("rev-parse", "HEAD").decode().strip()
        diff = git("diff", "--binary", "HEAD")
        for path in extra:
            result = subprocess.run(["git", "diff", "--no-index", "--binary", "/dev/null", path], cwd=repo, capture_output=True)
            if result.returncode != 1: raise RuntimeError(f"Unable to export {path}")
            diff += result.stdout
        # Validate against clean upstream sources, never the user's runtime/configuration.
        with tempfile.TemporaryDirectory(prefix="xiaomu-patch-check-") as temp:
            with tarfile.open(fileobj=io.BytesIO(git("archive", "HEAD"))) as archive:
                archive.extractall(temp, filter="data")
            subprocess.run(["git", "apply", "--check", "-"], input=diff, cwd=temp, check=True)
        patch = folder / f"{name}-macos.patch"
        patch.write_bytes(diff)
        for legal in ("LICENSE", "NOTICE"):
            (folder / f"{name}.{legal}").write_bytes((repo / legal).read_bytes())
        manifest.append({"repository": f"https://github.com/cyber-xiaoya/{name}", "commit": sha,
            "patch": patch.name, "sha256": hashlib.sha256(diff).hexdigest(), "clean_base_apply_check": True})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("已导出并在干净上游基线上验证两份源码补丁；不含配置、密钥和素材。")

if __name__ == "__main__": main()
