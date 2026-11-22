"""Optional exact, scope-filtered pgvector gallery using the existing SQLAlchemy session."""

import json

import numpy as np
from sqlalchemy import text


class PgVectorMatcher:
    def __init__(self, factory, company, site, model_id, fingerprint, local, sources):
        self.factory, self.company, self.site = factory, company, site
        self.model_id, self.fingerprint, self.local = model_id, fingerprint, local
        self.matrix, self.employee_ids = local.matrix, local.employee_ids
        scope = {"company": company, "site": site, "model": model_id, "version": fingerprint}
        with factory() as db:
            if db.get_bind().dialect.name != "postgresql":
                raise ValueError(
                    "pgvector backend requires PostgreSQL with the vector extension and migration applied"
                )
            db.execute(
                text("DELETE FROM employee_embeddings WHERE company_id=:company AND site_id=:site"),
                scope,
            )
            rows = [
                {
                    **scope,
                    "employee": str(employee),
                    "sample": source["sample_id"],
                    "variant": source["kind"],
                    "pose": source.get("pose_type", "front"),
                    "vector": json.dumps(vector.tolist(), separators=(",", ":")),
                }
                for employee, vector, source in zip(local.ids, local.matrix, sources, strict=True)
            ]
            if rows:
                db.execute(
                    text("""INSERT INTO employee_embeddings
                    (company_id,site_id,model_id,gallery_version,employee_id,sample_id,variant_kind,pose_type,embedding)
                    VALUES (:company,:site,:model,:version,:employee,:sample,:variant,:pose,CAST(:vector AS vector))"""),
                    rows,
                )
            db.commit()

    def match(self, embedding, yaw_ratio):
        vector = np.asarray(embedding, np.float32).reshape(-1)
        norm = np.linalg.norm(vector)
        if vector.shape != (512,) or not np.isfinite(vector).all() or norm < 1e-8:
            raise ValueError("Expected a finite 512-D embedding")
        # Top two DIFFERENT employees, each ranked by its best unaveraged sample.
        # Exact grouped query deliberately avoids approximate-HNSW runner-up errors.
        with self.factory() as db:
            scores = db.execute(
                text("""SELECT employee_id, MAX(1-(embedding <=> CAST(:vector AS vector))) AS similarity
                FROM employee_embeddings WHERE company_id=:company AND site_id=:site
                AND model_id=:model AND gallery_version=:version GROUP BY employee_id
                ORDER BY similarity DESC, employee_id LIMIT 2"""),
                {
                    "vector": json.dumps((vector / norm).tolist()),
                    "company": self.company,
                    "site": self.site,
                    "model": self.model_id,
                    "version": self.fingerprint,
                },
            ).all()
        if not scores:
            return None, 0.0, 0.0
        employee, best = scores[0]
        best = float(best)
        gap = best - (float(scores[1][1]) if len(scores) > 1 else 0.0)
        threshold, margin = (
            (self.local.frontal_threshold, self.local.frontal_margin)
            if yaw_ratio >= 0.5
            else (self.local.side_threshold, self.local.side_margin)
        )
        return (str(employee) if best >= threshold and gap >= margin else None), best, gap
