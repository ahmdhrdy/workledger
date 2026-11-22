# Installed inference models

The project runs pretrained models locally; it does not claim to have trained a new face/person model.

| Model | Purpose | Official source |
|---|---|---|
| RetinaFace `retinaface-resnet50.onnx` | Face boxes and five landmarks, ONNX Runtime CPU | Supplied locally; raw Biubug6-style loc/conf/landmark export verified from installed tensors |
| AdaFace `adaface_ir50_ms1mv2.onnx` | BGR 112x112 input, normalized 512-D output | Supplied locally; [AdaFace preprocessing](https://github.com/mk-minchul/AdaFace/blob/master/inference.py) |
| YOLOX-Nano `yolox_nano.onnx` | Person boxes and counts, ONNX Runtime CPU | [Official ONNX export](https://github.com/Megvii-BaseDetection/YOLOX/tree/main/demo/ONNXRuntime) |
| `person-reidentification-retail-0288` FP16 | Body embeddings | [Intel Open Model Zoo](https://github.com/openvinotoolkit/open_model_zoo/tree/464159ff32fbd2469b9ebf4e388eb0ebb63db9ee/models/intel/person-reidentification-retail-0288) |

Installer verification records SHA-256 hashes of the supplied face files and checks OpenVINO person/body downloads against pinned upstream manifests. Runtime enrollment tags include the AdaFace file hash and preprocessing version. Local provenance is recorded in `.local/models/manifest.json` when the installer runs.

The face ONNX files were supplied locally. Their filenames and hashes identify the installed artifacts. Upstream project references document the preprocessing and detector decoding used here.

AdaFace input preserves OpenCV BGR channel order, subtracts 127.5 and divides by 127.5. Five-point similarity alignment produces 112x112 pixels. The supplied export already normalizes its 512-D output. Enrollment originals, flips and opt-in synthetic masks use this same path.

General benchmark figures do not establish masked-face, attendance, anti-spoofing or camera-specific accuracy. Body-only matches remain tentative and cannot create attendance events.

Historical SFace latency and compatibility measurements are retained in PERFORMANCE.md. Measure the current RetinaFace/AdaFace pipeline independently on representative concurrent camera streams.

YOLOX-Nano uses a letterboxed 416x416 BGR float32 input in the 0..255 range, COCO person scores and non-maximum suppression. The pinned release SHA-256 is `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d`. Its license is retained as `.local/models/YOLOX-LICENSE.txt`.
