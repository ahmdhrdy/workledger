"""Publish immutable, validated NumPy gallery generations per company and site."""

import hashlib
import json
import os
import threading
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np

from .pose_matcher import PoseAdaptiveMatcher


class GalleryIndex:
    def __init__(self, directory, model_id, settings, factory=None):
        self.factory = factory
        self.root = Path(directory) / "adaface"
        self.model_id, self.settings = model_id, settings
        self.lock = threading.RLock()
        self.entries = {}

    def update(self, company, site, templates):
        scope = (company, site)
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    (
                        sample["id"],
                        sample["vector"],
                        sample.get("augmentations", []),
                        sample.get("variant", "front"),
                    )
                    for sample in sorted(templates, key=lambda sample: sample["id"])
                ],
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self.lock:
            old = self.entries.get(scope)
            if old and old["fingerprint"] == fingerprint:
                return old
            ids, vectors, sources = [], [], []
            for sample in templates:
                variants = [{"kind": "original", "vector": sample["vector"]}]
                variants.extend(sample.get("augmentations", []))
                for variant in variants:
                    ids.append(sample["employee_id"])
                    vectors.append(variant["vector"])
                    pose = sample.get("variant", "front")
                    if pose == "uncovered":
                        pose = "front"
                    if variant["kind"] == "horizontal_flip":
                        pose = {"left_30": "right_30", "right_30": "left_30"}.get(pose, pose)
                    if variant["kind"].startswith("synthetic_mask"):
                        pose = "masked"
                    sources.append(
                        {"sample_id": sample["id"], "kind": variant["kind"], "pose_type": pose}
                    )
            matrix = np.asarray(vectors, dtype=np.float32).reshape(-1, 512)
            matcher = PoseAdaptiveMatcher(
                ids,
                matrix,
                self.settings.face_threshold,
                self.settings.face_margin,
                self.settings.side_face_threshold,
                self.settings.side_face_margin,
            )
            if self.settings.gallery_backend == "pgvector":
                if self.factory is None:
                    raise ValueError("pgvector requires a database session factory")
                from .pgvector_gallery import PgVectorMatcher

                matcher = PgVectorMatcher(
                    self.factory, company, site, self.model_id, fingerprint, matcher, sources
                )
            # Scope components originate from database UUIDs, not request path strings.
            scope_dir = self.root / str(company) / str(site)
            pointer = scope_dir / "CURRENT"
            if pointer.exists():
                try:
                    previous = str(UUID(pointer.read_text(encoding="utf-8").strip()))
                    existing = scope_dir / "generations" / previous
                    manifest = json.loads((existing / "manifest.json").read_text(encoding="utf-8"))
                    if (
                        manifest["model_id"] == self.model_id
                        and manifest["fingerprint"] == fingerprint
                        and np.array_equal(
                            np.load(existing / "emp_ids.npy", allow_pickle=False),
                            np.asarray(ids, dtype=str),
                        )
                        and np.array_equal(
                            np.load(existing / "emp_embeddings.npy", allow_pickle=False),
                            matcher.matrix,
                        )
                    ):
                        entry = {**manifest, "matcher": matcher, "path": str(existing)}
                        self.entries[scope] = entry
                        return entry
                except (OSError, ValueError, KeyError):
                    pass
            version = str(uuid4())
            generation = scope_dir / "generations" / version
            generation.mkdir(parents=True, exist_ok=False)
            np.save(generation / "emp_ids.npy", np.asarray(ids, dtype=str), allow_pickle=False)
            np.save(generation / "emp_embeddings.npy", matcher.matrix, allow_pickle=False)
            loaded_ids = np.load(generation / "emp_ids.npy", allow_pickle=False)
            loaded_matrix = np.load(generation / "emp_embeddings.npy", allow_pickle=False)
            if loaded_matrix.shape != (len(loaded_ids), 512):
                raise ValueError("Gallery validation failed")
            manifest = {
                "version": version,
                "model_id": self.model_id,
                "fingerprint": fingerprint,
                "samples": len(templates),
                "embeddings": len(ids),
                "sources": sources,
                "synthetic_masks_enabled": any(
                    source["kind"].startswith("synthetic_mask") for source in sources
                ),
            }
            (generation / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            temporary = scope_dir / f"CURRENT.{version}.tmp"
            temporary.write_text(version, encoding="utf-8")
            os.replace(temporary, scope_dir / "CURRENT")
            entry = {**manifest, "matcher": matcher, "path": str(generation)}
            self.entries[scope] = entry
            return entry

    def invalidate(self, company, site):
        with self.lock:
            self.entries.pop((company, site), None)
