import { useEffect, useRef, useState } from "react";

type Photo = {
  id: string;
  file: File;
  url: string;
  selected: boolean;
  state: "ready" | "processing" | "saved" | "duplicate" | "rejected";
  message: string;
};

export type EnrollmentSample = {
  id: string;
  variant: string;
  source_name?: string;
  quality?: number;
  detection_score?: number;
  created_at?: string;
  has_preview?: boolean;
  model?: string;
  augmentations?: { kind: string }[];
};

export function EnrollmentPhotos({
  employeeId,
  variant,
  disabled,
  onComplete,
  onBusyChange,
}: {
  employeeId: string;
  variant: string;
  disabled: boolean;
  onComplete: () => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [running, setRunning] = useState(false);
  const [maskedAugmentation, setMaskedAugmentation] = useState(false);
  const [message, setMessage] = useState("");
  const urls = useRef<string[]>([]);
  useEffect(
    () => () => urls.current.forEach((url) => URL.revokeObjectURL(url)),
    [],
  );
  const pending = photos.filter(
    (photo) => photo.selected && !["saved", "duplicate"].includes(photo.state),
  );

  function choose(files: FileList | null) {
    urls.current.forEach((url) => URL.revokeObjectURL(url));
    const items = Array.from(files ?? []).map((file) => {
      const valid =
        file.size <= 5_000_000 &&
        ["image/jpeg", "image/png", "image/webp"].includes(file.type);
      return {
        id: crypto.randomUUID(),
        file,
        url: URL.createObjectURL(file),
        selected: valid,
        state: valid ? ("ready" as const) : ("rejected" as const),
        message: valid
          ? "Ready for review"
          : "Use JPEG, PNG or WebP under 5 MB",
      };
    });
    urls.current = items.map((photo) => photo.url);
    setPhotos(items);
    setMessage("");
  }

  async function process() {
    if (!employeeId || !pending.length || running) return;
    setRunning(true);
    onBusyChange(true);
    setMessage("");
    let saved = 0,
      rejected = 0,
      duplicates = 0;
    function update(id: string, values: Partial<Photo>) {
      setPhotos((current) =>
        current.map((photo) =>
          photo.id === id ? { ...photo, ...values } : photo,
        ),
      );
    }
    try {
      for (const photo of pending) {
        update(photo.id, {
          state: "processing",
          message: "Detecting, validating and extracting embedding…",
        });
        try {
          const body = new FormData();
          body.set("image", photo.file);
          body.set("augment_masked", String(maskedAugmentation));
          const response = await fetch(
            `/api/v1/local/employees/${encodeURIComponent(employeeId)}/enroll?variant=${encodeURIComponent(variant)}`,
            {
              method: "POST",
              credentials: "same-origin",
              headers: { "X-Workledger-Request": "1" },
              body,
            },
          );
          const result = await response.json();
          if (!response.ok)
            throw new Error(
              typeof result.detail === "string"
                ? result.detail
                : "Enrollment failed",
            );
          if (result.duplicate) duplicates++;
          else saved++;
          update(photo.id, {
            state: result.duplicate ? "duplicate" : "saved",
            selected: false,
            message: `${result.duplicate ? "Already in gallery" : "Saved to recognition gallery"} · Detection ${(result.detection_score * 100).toFixed(1)}% · Sharpness ${result.quality}`,
          });
        } catch (error) {
          rejected++;
          update(photo.id, {
            state: "rejected",
            message: error instanceof Error ? error.message : "Upload failed",
          });
        }
      }
      setMessage(
        `${saved} saved · ${duplicates} already saved · ${rejected} rejected. Accepted samples are available for recognition immediately.`,
      );
      await onComplete();
    } catch (error) {
      setMessage(
        `Uploads processed. Gallery refresh failed: ${error instanceof Error ? error.message : "Refresh the page"}`,
      );
    } finally {
      setRunning(false);
      onBusyChange(false);
    }
  }

  return (
    <div>
      <label className="vision-upload">
        <strong>Choose multiple employee photos</strong>
        <span>
          Review and select photos below · JPEG/PNG/WebP · under 5 MB each · up
          to 20 saved samples per employee
        </span>
        <input
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp"
          disabled={disabled || running}
          aria-label="Face photos"
          onChange={(event) => choose(event.target.files)}
        />
      </label>
      <p className="fineprint">
        Selected photos build this employee’s embedding gallery using the
        pretrained AdaFace model. Model weights stay fixed. Encrypted thumbnails
        are retained so you can review and remove saved samples.
      </p>
      <label className="checkline" style={{ marginBottom: 12 }}>
        <input
          type="checkbox"
          checked={maskedAugmentation}
          disabled={disabled || running}
          onChange={(event) => setMaskedAugmentation(event.target.checked)}
        />
        Include synthetic masked variants (experimental). Original and flipped
        faces are included by default.
      </label>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
          gap: 12,
          marginBottom: 16,
        }}
      >
        {photos.map((photo) => (
          <article
            key={photo.id}
            style={{
              border: "1px solid #dbe4d6",
              borderRadius: 8,
              padding: 10,
            }}
          >
            <img
              src={photo.url}
              alt={photo.file.name}
              style={{ width: "100%", height: 130, objectFit: "contain" }}
            />
            <label
              style={{
                display: "flex",
                gap: 6,
                alignItems: "start",
                overflowWrap: "anywhere",
              }}
            >
              <input
                type="checkbox"
                checked={photo.selected}
                disabled={
                  disabled ||
                  running ||
                  ["saved", "duplicate"].includes(photo.state)
                }
                onChange={(event) =>
                  setPhotos((current) =>
                    current.map((item) =>
                      item.id === photo.id
                        ? { ...item, selected: event.target.checked }
                        : item,
                    ),
                  )
                }
              />
              {photo.file.name}
            </label>
            <small
              role="status"
              style={{
                display: "block",
                color: photo.state === "rejected" ? "#ad3426" : "#3d5940",
              }}
            >
              {photo.message}
            </small>
          </article>
        ))}
      </div>
      <button
        type="button"
        className="button primary"
        disabled={disabled || running || !pending.length || !employeeId}
        onClick={() => void process()}
      >
        {running
          ? "Processing selected photos…"
          : `Build gallery from ${pending.length} selected photo${pending.length === 1 ? "" : "s"}`}
      </button>
      {message && <p role="status">{message}</p>}
    </div>
  );
}

export function SavedEnrollmentSamples({
  employeeId,
  samples,
  disabled,
  onRemove,
}: {
  employeeId: string;
  samples: EnrollmentSample[];
  disabled: boolean;
  onRemove: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!samples.length) return null;
  return (
    <details
      onToggle={(event) => setOpen(event.currentTarget.open)}
      style={{ padding: "0 20px 16px" }}
    >
      <summary style={{ cursor: "pointer" }}>
        Review {samples.length} saved sample{samples.length === 1 ? "" : "s"}
      </summary>
      {open && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 12,
            marginTop: 12,
          }}
        >
          {samples.map((sample) => (
            <article
              key={sample.id}
              style={{
                border: "1px solid #dbe4d6",
                borderRadius: 8,
                padding: 10,
                overflowWrap: "anywhere",
              }}
            >
              {sample.has_preview ? (
                <a
                  href={`/api/v1/local/employees/${employeeId}/enrollment/${sample.id}/image`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    src={`/api/v1/local/employees/${employeeId}/enrollment/${sample.id}/image`}
                    alt={sample.source_name ?? "Saved face sample"}
                    style={{ width: "100%", height: 140, objectFit: "contain" }}
                  />
                </a>
              ) : (
                <p className="fineprint">
                  Earlier sample. Review image was not retained.
                </p>
              )}
              <small style={{ display: "block" }}>
                {sample.source_name ?? "Earlier enrollment"}
              </small>
              <small style={{ display: "block" }}>
                {sample.variant} ·{" "}
                {sample.quality != null
                  ? `Sharpness ${sample.quality}`
                  : "Quality unavailable"}
              </small>
              {sample.created_at && (
                <small style={{ display: "block" }}>
                  {new Date(sample.created_at).toLocaleString()}
                </small>
              )}
              {sample.augmentations?.map((variant) => (
                <details key={variant.kind}>
                  <summary>{variant.kind.replaceAll("_", " ")}</summary>
                  <img
                    src={`/api/v1/local/employees/${employeeId}/enrollment/${sample.id}/image?augmentation=${encodeURIComponent(variant.kind)}`}
                    alt={variant.kind}
                    loading="lazy"
                    style={{ width: "100%", height: 112, objectFit: "contain" }}
                  />
                </details>
              ))}
              <button
                type="button"
                className="text-button"
                disabled={disabled}
                onClick={() => onRemove(sample.id)}
              >
                Remove sample
              </button>
            </article>
          ))}
        </div>
      )}
    </details>
  );
}
