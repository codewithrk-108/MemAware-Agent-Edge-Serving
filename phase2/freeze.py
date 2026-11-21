"""Freeze and verify Phase 2 policy inputs before evaluation."""

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_policy(policy: dict, estimator_path: Path, development_manifest: Path,
                  output: Path) -> dict:
    artifact = {
        "policy": policy,
        "runtime_estimator": {
            "path": estimator_path.as_posix(),
            "sha256": sha256(estimator_path),
        },
        "development_manifest": {
            "path": development_manifest.as_posix(),
            "sha256": sha256(development_manifest),
        },
    }
    canonical = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["freeze_sha256"] = hashlib.sha256(canonical).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return artifact


def verify_frozen_policy(path: Path) -> dict:
    artifact = json.loads(path.read_text())
    expected = artifact.pop("freeze_sha256")
    canonical = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != expected:
        raise ValueError("frozen policy checksum does not match its contents")
    for key in ("runtime_estimator", "development_manifest"):
        item = artifact[key]
        target = Path(item["path"])
        if not target.exists() or sha256(target) != item["sha256"]:
            raise ValueError(f"frozen {key} input is missing or modified: {target}")
    artifact["freeze_sha256"] = expected
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("create")
    freeze.add_argument("--policy", type=Path, required=True)
    freeze.add_argument("--estimator", type=Path, required=True)
    freeze.add_argument("--development-manifest", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("artifact", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        artifact = freeze_policy(json.loads(args.policy.read_text()), args.estimator,
                                 args.development_manifest, args.out)
    else:
        artifact = verify_frozen_policy(args.artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

