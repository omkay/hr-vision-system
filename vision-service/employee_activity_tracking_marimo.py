# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "marimo",
#     "numpy",
#     "pandas",
#     "matplotlib",
#     "seaborn",
#     "opencv-python-headless",
#     "tqdm",
#     "shapely",
#     "scikit-learn",
#     "ultralytics==8.2.50",
#     "insightface==0.7.3",
#     "onnxruntime==1.18.0",
#     "torchreid==0.2.5",
#     "torch",
#     "torchvision",
#     "networkx",
# ]
# ///
"""
Employee Activity Tracking — reactive edition (marimo)
======================================================

A marimo port of the Jupyter research notebook. The pipeline is identical
(YOLOv8 + ByteTrack + InsightFace + TorchReID/OSNet + rule-based event engine),
but marimo's reactive DAG gives us:

* Live sliders for detection / matching thresholds — every downstream cell
  re-runs automatically when you drag.
* A dropdown to switch between videos without touching code.
* A "Run pipeline" button that produces an annotated clip and a DataFrame of
  events you can filter interactively.

Run with:   marimo edit employee_activity_tracking_marimo.py
Or view as an app:   marimo run employee_activity_tracking_marimo.py
"""

import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium", app_title="Employee Activity Tracking")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Employee Activity Tracking — reactive edition
    *Graduation research — Omar Khairat*

    A multi-modal CV pipeline that turns office surveillance video into a
    structured event log per employee (**presence**, **phone use**,
    **interactions**).

    | Stage | Model |
    |-------|-------|
    | Person / phone / laptop detection | YOLOv8 |
    | Multi-object tracking | ByteTrack |
    | Face recognition | InsightFace (ArcFace) |
    | Person Re-ID | TorchReID (OSNet) |
    | Identity fusion | custom temporal voting |
    | Event engine | rule-based state machines |
    """)
    return


@app.cell
def _():
    import os, sys, math, json, time, collections, dataclasses, warnings, base64
    from pathlib import Path
    from typing import Optional, List, Dict, Tuple
    import numpy as np
    import pandas as pd
    import cv2
    import matplotlib.pyplot as plt
    import seaborn as sns
    warnings.filterwarnings("ignore")
    sns.set_theme(style="whitegrid")

    try:
        import torch
        DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        torch = None
        DEVICE = "cpu"
    return (
        DEVICE,
        Dict,
        List,
        Optional,
        Path,
        Tuple,
        base64,
        collections,
        cv2,
        dataclasses,
        json,
        math,
        np,
        os,
        pd,
        plt,
        sns,
        torch,
    )


@app.cell
def _(mo):
    mo.md("""
    ## 1 · Configuration
    """)
    return


@app.cell
def _(Path, os):
    def _resolve_project_dir() -> Path:
        env = os.environ.get("PROJECT_DIR")
        if env:
            return Path(env).expanduser().resolve()
        here = Path.cwd().resolve()
        for candidate in [here, *here.parents]:
            if (candidate / "employee_activity_tracking.ipynb").exists():
                return candidate
        return here

    PROJECT_DIR = _resolve_project_dir()
    DATA_DIR    = PROJECT_DIR / "data"
    OUT_DIR     = PROJECT_DIR / "outputs";  OUT_DIR.mkdir(parents=True, exist_ok=True)
    GALLERY_DIR = PROJECT_DIR / "gallery";  GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR  = PROJECT_DIR / "models";   MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR, GALLERY_DIR, OUT_DIR


@app.cell
def _(mo):
    # --- live tunables ------------------------------------------------------
    det_conf   = mo.ui.slider(0.10, 0.90, 0.05, 0.30, label="YOLO detection conf")
    det_iou    = mo.ui.slider(0.30, 0.90, 0.05, 0.50, label="NMS IoU")
    face_thr   = mo.ui.slider(0.20, 0.80, 0.01, 0.45, label="Face-match threshold")
    reid_thr   = mo.ui.slider(0.50, 0.95, 0.01, 0.75, label="ReID-match threshold")
    fuse_win   = mo.ui.slider(5, 120, 1, 30,    label="Identity-voting window (frames)")
    stride     = mo.ui.slider(1, 10, 1, 2,      label="Frame stride")
    max_frames = mo.ui.number(50, 5000, 10, value=600, label="Max frames to process (smoke-test)")
    prox_px    = mo.ui.slider(60, 400, 10, 180, label="Proximity (px) for interaction")

    ui = mo.vstack([
        mo.hstack([det_conf, det_iou], justify="start"),
        mo.hstack([face_thr, reid_thr], justify="start"),
        mo.hstack([fuse_win, stride], justify="start"),
        mo.hstack([max_frames, prox_px], justify="start"),
    ])
    ui
    return (
        det_conf,
        det_iou,
        face_thr,
        fuse_win,
        max_frames,
        prox_px,
        reid_thr,
        stride,
    )


@app.cell
def _(mo):
    mo.md("""
    ## 2 · Data exploration
    """)
    return


@app.cell
def _(DATA_DIR, Path, cv2, pd):
    def probe_video(path: Path) -> dict:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return {"path": str(path), "readable": False}
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        cap.release()
        return {"name": path.name, "w": w, "h": h, "frames": n,
                "fps": round(fps, 2),
                "duration_s": round(n / fps, 1) if fps else None,
                "size_mb": round(path.stat().st_size / 1e6, 1)}

    videos = sorted([p for p in DATA_DIR.glob("*.mp4")])
    meta = pd.DataFrame([probe_video(p) for p in videos])
    meta
    return (videos,)


@app.cell
def _(mo, videos):
    # Dropdown to pick which video to explore / process
    video_picker = mo.ui.dropdown(
        options={v.name: str(v) for v in videos},
        value=videos[0].name if videos else None,
        label="Video to analyse",
    )
    video_picker
    return (video_picker,)


@app.cell
def _(Path, cv2, mo, plt, video_picker):
    """Show a sample frame from the currently selected video."""
    _path_str = video_picker.value
    if not _path_str:
        _output = mo.md("⚠ No video selected.")
    else:
        _cap = cv2.VideoCapture(_path_str)
        _fps = _cap.get(cv2.CAP_PROP_FPS) or 25.0
        _cap.set(cv2.CAP_PROP_POS_FRAMES, int(_fps * 3.0))
        _ok, _frame = _cap.read()
        _cap.release()
        if _ok:
            _fig, _ax = plt.subplots(figsize=(8, 4.5))
            _ax.imshow(cv2.cvtColor(_frame, cv2.COLOR_BGR2RGB))
            _ax.set_title(Path(_path_str).name, fontsize=9)
            _ax.axis("off")
            _output = _fig
        else:
            _output = mo.md("Could not read a frame.")
    _output
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 3 · Zone Configuration
    Define activity zones (desks, common areas) for the selected video.
    Zones are stored per-video and persisted to `outputs/zones_config.json`.
    """)
    return


@app.cell
def _(Dict, List, Path, json):
    """Zone configuration helpers."""

    def load_zones_config(config_path: Path) -> Dict[str, List[tuple]]:
        """Load zones configuration from JSON file."""
        if config_path.exists():
            with open(config_path, 'r') as f:
                data = json.load(f)
                # Convert lists back to tuples
                return {k: [tuple(z) for z in v] for k, v in data.items()}
        return {}

    def save_zones_config(config_path: Path, zones_dict: Dict[str, List[tuple]]):
        """Save zones configuration to JSON file."""
        with open(config_path, 'w') as f:
            json.dump(zones_dict, f, indent=2)

    def default_zones_for_video(video_name: str) -> List[tuple]:
        """Return default zones - can be customized per video."""
        # Default office layout zones
        return [
            ("desk_A",   50, 200,  500, 650),
            ("desk_B",  550, 200, 1000, 650),
            ("common", 1020, 200, 1900, 1050),
        ]

    return default_zones_for_video, load_zones_config, save_zones_config


@app.cell
def _(OUT_DIR, Path, load_zones_config, video_picker):
    """Load or initialize zones for the selected video."""
    zones_config_path = OUT_DIR / "zones_config.json"
    all_zones_config = load_zones_config(zones_config_path)

    _video_name = Path(video_picker.value).name if video_picker.value else "default"
    current_video_zones_raw = all_zones_config.get(_video_name, [])
    return all_zones_config, current_video_zones_raw, zones_config_path


@app.cell
def _(
    OUT_DIR,
    Path,
    base64,
    current_video_zones_raw,
    cv2,
    default_zones_for_video,
    json,
    mo,
    video_picker,
):
    _path_str = video_picker.value
    _vname = Path(_path_str).name if _path_str else "default"
    _init = (
        current_video_zones_raw
        if current_video_zones_raw
        else default_zones_for_video(_vname)
    )
    _html_path = OUT_DIR / "zone_drawer.html"
    _tmpl_path = Path(__file__).parent / "zone_drawer_template.html"
    _wrote = False

    if _path_str and _tmpl_path.exists():
        _cap = cv2.VideoCapture(_path_str)
        _fps = _cap.get(cv2.CAP_PROP_FPS) or 25.0
        _cap.set(cv2.CAP_PROP_POS_FRAMES, int(_fps * 3.0))
        _ok, _frame = _cap.read()
        _cap.release()
        if _ok:
            _fh, _fw = _frame.shape[:2]
            _dw = min(_fw, 1200)
            _dh = int(_fh * _dw / _fw)
            _, _buf = cv2.imencode(".jpg", _frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            _b64 = base64.b64encode(_buf).decode()
            _seed = json.dumps(_init)
            _html = (
                _tmpl_path.read_text(encoding="utf-8")
                .replace("__VNAME__", _vname)
                .replace("__FW__", str(_fw))
                .replace("__FH__", str(_fh))
                .replace("__DW__", str(_dw))
                .replace("__DH__", str(_dh))
                .replace("__SEED__", _seed)
                .replace("__B64__", _b64)
            )
            _html_path.write_text(_html, encoding="utf-8")
            _wrote = True

    zones_export = mo.ui.text_area(
        value=json.dumps(_init, indent=2),
        label="Zones JSON — paste here after copying from the zone drawer",
        rows=7,
        full_width=True,
    )

    if _wrote:
        _link = f"file://{_html_path.resolve()}"
        _ui = mo.vstack([
            mo.md(
                f"### Zone Drawer\n\n"
                f"**Step 1** — [Open Zone Drawer]({_link}) in a new tab "
                f"*(right-click → Open Link in New Tab)*\n\n"
                f"**Step 2** — Click first corner on the frame, then the opposite corner, enter name. Repeat per zone.\n\n"
                f"**Step 3** — Click **Copy Zones JSON** in the drawer\n\n"
                f"**Step 4** — Paste into the text area below, then click **Save zones**"
            ),
            zones_export,
        ])
    elif not _tmpl_path.exists():
        _ui = mo.md("⚠ `zone_drawer_template.html` not found next to the notebook.")
    else:
        _ui = mo.vstack([
            mo.md("Select a video first — the zone drawer link will appear here."),
            zones_export,
        ])

    _ui
    return (zones_export,)


@app.cell
def _(json, mo, zones_export):
    """Parse and expose zones_parsed for downstream cells."""
    zones_parsed = []
    zones_parse_error = None
    try:
        _raw = json.loads(zones_export.value)
        if not isinstance(_raw, list):
            raise ValueError("Expected a list")
        for z in _raw:
            if not isinstance(z, (list, tuple)) or len(z) != 5:
                raise ValueError(f"Bad zone: {z}")
        zones_parsed = [tuple(z) for z in _raw]
    except Exception as _e:
        zones_parse_error = str(_e)

    if zones_parse_error:
        mo.md(f"⚠️ {zones_parse_error}")
    elif zones_parsed:
        _lines = "\n".join(
            f"{i+1}. **{z[0]}** ({z[1]}, {z[2]}) → ({z[3]}, {z[4]})"
            for i, z in enumerate(zones_parsed)
        )
        mo.md(f"✓ **{len(zones_parsed)} zones active:**\n\n{_lines}")
    else:
        mo.md("*No zones yet.*")
    return zones_parse_error, zones_parsed


@app.cell
def _(mo):
    save_zones_btn = mo.ui.button(label="💾 Save zones for this video", kind="success")
    save_zones_btn
    return (save_zones_btn,)


@app.cell
def _(
    Path,
    all_zones_config,
    mo,
    save_zones_btn,
    save_zones_config,
    video_picker,
    zones_config_path,
    zones_parse_error,
    zones_parsed,
):
    """Save zones configuration when button is clicked."""
    save_status = mo.md("")

    if save_zones_btn.value and not zones_parse_error:
        _video_name = Path(video_picker.value).name if video_picker.value else "default"
        all_zones_config[_video_name] = zones_parsed
        save_zones_config(zones_config_path, all_zones_config)
        save_status = mo.md(f"✓ Zones saved for **{_video_name}** to `{zones_config_path.name}`")
    elif save_zones_btn.value and zones_parse_error:
        save_status = mo.md("❌ Cannot save - fix parse errors first")

    save_status
    return


@app.cell
def _(Path, cv2, mo, plt, video_picker, zones_parse_error, zones_parsed):
    """Preview zones on sample frame with coordinate grid."""
    _path_str = video_picker.value

    if not _path_str:
        _zone_preview = mo.md("⚠ No video selected.")
    elif zones_parse_error:
        _zone_preview = mo.md("⚠ Fix zone configuration errors to see preview.")
    else:
        # import numpy as np
        _cap = cv2.VideoCapture(_path_str)
        _fps = _cap.get(cv2.CAP_PROP_FPS) or 25.0
        _cap.set(cv2.CAP_PROP_POS_FRAMES, int(_fps * 3.0))
        _ok, _frame = _cap.read()
        _cap.release()

        if _ok:
            # Draw zones on frame
            _vis = _frame.copy()
            _h, _w = _frame.shape[:2]
            _colors = [(255, 100, 100), (100, 255, 100), (100, 100, 255),
                      (255, 255, 100), (255, 100, 255), (100, 255, 255)]

            # Draw coordinate grid for reference
            _grid_step = max(_w, _h) // 8
            for _x in range(0, _w, _grid_step):
                cv2.line(_vis, (_x, 0), (_x, _h), (80, 80, 80), 1, cv2.LINE_AA)
                cv2.putText(_vis, str(_x), (_x + 5, 20),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            for _y in range(0, _h, _grid_step):
                cv2.line(_vis, (0, _y), (_w, _y), (80, 80, 80), 1, cv2.LINE_AA)
                cv2.putText(_vis, str(_y), (5, _y + 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)

            # Draw zones
            for _idx, _z in enumerate(zones_parsed):
                _name, _x1, _y1, _x2, _y2 = _z
                _x1, _y1, _x2, _y2 = int(_x1), int(_y1), int(_x2), int(_y2)
                _color = _colors[_idx % len(_colors)]
                cv2.rectangle(_vis, (_x1, _y1), (_x2, _y2), _color, 4)
                # Draw zone label with background
                _label = f"{_idx+1}. {_name}"
                _text_size = cv2.getTextSize(_label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
                cv2.rectangle(_vis, (_x1, _y1 - _text_size[1] - 10),
                             (_x1 + _text_size[0] + 10, _y1), _color, -1)
                cv2.putText(_vis, _label, (_x1 + 5, _y1 - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

            _fig, _ax = plt.subplots(figsize=(14, 8))
            _ax.imshow(cv2.cvtColor(_vis, cv2.COLOR_BGR2RGB))
            _ax.set_title(f"Zone Preview: {Path(_path_str).name} | Frame size: {_w}×{_h}", fontsize=12)
            _ax.set_xlabel("X coordinate (pixels)", fontsize=10)
            _ax.set_ylabel("Y coordinate (pixels)", fontsize=10)
            _zone_preview = _fig
        else:
            _zone_preview = mo.md("Could not read frame for preview.")

    _zone_preview
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 4 · Identity — enrollment
    Drop reference images into
    `gallery/<employee_name>/face/` and `gallery/<employee_name>/body/`
    (multiple images per person, different angles / days / clothing).
    """)
    return


@app.cell
def _(DEVICE, Optional, np):
    class FaceEmbedder:
        """Wraps InsightFace's buffalo_l bundle (detector + ArcFace)."""
        def __init__(self, device: str = DEVICE):
            from insightface.app import FaceAnalysis
            providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                         if device == "cuda" else ["CPUExecutionProvider"])
            self.app = FaceAnalysis(name="buffalo_l", providers=providers)
            self.app.prepare(ctx_id=0 if device == "cuda" else -1,
                             det_size=(640, 640))

        def embed(self, bgr_image: np.ndarray) -> Optional[np.ndarray]:
            faces = self.app.get(bgr_image)
            if not faces:
                return None
            f = max(faces, key=lambda x:
                    (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))
            return f.normed_embedding.astype(np.float32)

        def embed_many(self, bgr_image: np.ndarray):
            return [(f.bbox.astype(int), f.normed_embedding.astype(np.float32))
                    for f in self.app.get(bgr_image)]

    return (FaceEmbedder,)


@app.cell
def _(DEVICE, List, cv2, np, torch):
    class ReIDEmbedder:
        """OSNet x1_0 trained on MSMT17 — produces 512-D body descriptors."""
        def __init__(self, device: str = DEVICE):
            import torchreid
            import torchvision.transforms as T
            self.device = device
            self.model = torchreid.models.build_model(
                name="osnet_x1_0", num_classes=1000, pretrained=True
            ).to(device).eval()
            self.tf = T.Compose([
                T.ToPILImage(), T.Resize((256, 128)), T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406],
                            std =[0.229, 0.224, 0.225]),
            ])

        @torch.inference_mode() if torch is not None else (lambda f: f)
        def embed(self, bgr_crop: np.ndarray) -> np.ndarray:
            rgb = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)
            x = self.tf(rgb).unsqueeze(0).to(self.device)
            v = self.model(x).squeeze(0).cpu().numpy()
            return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)

        @torch.inference_mode() if torch is not None else (lambda f: f)
        def embed_batch(self, bgr_crops: List[np.ndarray]) -> np.ndarray:
            if not bgr_crops:
                return np.zeros((0, 512), np.float32)
            xs = torch.stack([self.tf(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))
                              for c in bgr_crops]).to(self.device)
            v = self.model(xs).cpu().numpy()
            v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
            return v.astype(np.float32)

    return (ReIDEmbedder,)


@app.cell
def _(Dict, List, Path, dataclasses, np):
    @dataclasses.dataclass
    class EmployeeGallery:
        names:      List[str]
        face_vecs:  np.ndarray
        reid_banks: Dict[str, np.ndarray]

        def save(self, path: Path):
            np.savez(path, names=np.array(self.names), face=self.face_vecs,
                     reid_keys=np.array(list(self.reid_banks.keys())),
                     **{f"reid_{k}": v for k, v in self.reid_banks.items()})

        @staticmethod
        def load(path: Path) -> "EmployeeGallery":
            z = np.load(path, allow_pickle=True)
            return EmployeeGallery(
                names=z["names"].tolist(),
                face_vecs=z["face"],
                reid_banks={k: z[f"reid_{k}"] for k in z["reid_keys"]},
            )

    def build_gallery(gallery_dir, face_emb, reid_emb) -> EmployeeGallery:
        import cv2
        names, face_vecs, reid_banks = [], [], {}
        people = [p for p in sorted(gallery_dir.iterdir()) if p.is_dir()]
        if not people:
            raise RuntimeError(
                f"No sub-folders under {gallery_dir}. Add one folder per employee."
            )
        for person in people:
            face_dir, body_dir = person / "face", person / "body"
            fvs = []
            if face_dir.exists():
                for p in sorted(face_dir.glob("*.*")):
                    img = cv2.imread(str(p))
                    if img is None:
                        continue
                    v = face_emb.embed(img)
                    if v is not None:
                        fvs.append(v)
            if fvs:
                face_vec = np.mean(np.stack(fvs), axis=0)
                face_vec /= (np.linalg.norm(face_vec) + 1e-9)
            else:
                face_vec = np.zeros(512, np.float32)

            crops = []
            if body_dir.exists():
                for p in sorted(body_dir.glob("*.*")):
                    c = cv2.imread(str(p))
                    if c is not None:
                        crops.append(c)
            bank = reid_emb.embed_batch(crops) if crops else np.zeros((0, 512), np.float32)

            names.append(person.name)
            face_vecs.append(face_vec)
            reid_banks[person.name] = bank
            print(f"  + {person.name:20s}  faces:{len(fvs):2d}  body:{len(crops):2d}")
        return EmployeeGallery(names=names,
                               face_vecs=np.stack(face_vecs),
                               reid_banks=reid_banks)

    return EmployeeGallery, build_gallery


@app.cell
def _(mo):
    build_gallery_btn = mo.ui.button(label="Build / refresh gallery", kind="success")
    build_gallery_btn
    return (build_gallery_btn,)


@app.cell
def _(
    EmployeeGallery,
    FaceEmbedder,
    GALLERY_DIR,
    ReIDEmbedder,
    build_gallery,
    build_gallery_btn,
    mo,
):
    # Reactively rebuild the gallery when the user clicks the button.
    # Cached across clicks via a tuple (click_count, gallery).
    _ = build_gallery_btn.value  # forces dependency
    gallery_path = GALLERY_DIR / "gallery.npz"
    gallery = None
    face_emb = None
    reid_emb = None
    status = mo.md("*Gallery not built yet — click the button above once you have reference images in `gallery/<name>/face|body/`.*")
    if build_gallery_btn.value:
        try:
            face_emb = FaceEmbedder()
            reid_emb = ReIDEmbedder()
            gallery = build_gallery(GALLERY_DIR, face_emb, reid_emb)
            gallery.save(gallery_path)
            status = mo.md(
                f"✓ Gallery built — **{len(gallery.names)}** employees: "
                f"`{', '.join(gallery.names)}`"
            )
        except Exception as e:
            status = mo.md(f"❌ Enrollment failed: `{e}`")
    elif gallery_path.exists():
        # Initialize embedders when loading cached gallery too
        face_emb = FaceEmbedder()
        reid_emb = ReIDEmbedder()
        gallery = EmployeeGallery.load(gallery_path)
        status = mo.md(f"Loaded cached gallery: **{len(gallery.names)}** employees.")
    status
    return face_emb, gallery, reid_emb


@app.cell
def _(mo):
    mo.md("""
    ## 5 · Detection + tracking (YOLOv8 + ByteTrack)
    """)
    return


@app.cell
def _(DEVICE):
    import contextlib

    TARGET_CLASSES = {
        0: "person",
        67: "cell phone",
        63: "laptop",
        62: "monitor",  # COCO tv class as monitor proxy
    }

    @contextlib.contextmanager
    def _allow_legacy_torch_load():
        # Compatibility shim for older ultralytics with torch>=2.6.
        import torch
        orig = torch.load
        def _loader(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return orig(*args, **kwargs)
        torch.load = _loader
        try:
            yield
        finally:
            torch.load = orig

    class PersonObjectDetector:
        def __init__(self, weights="yolov8m.pt", device=DEVICE, conf=0.30, iou=0.50):
            from ultralytics import YOLO
            with _allow_legacy_torch_load():
                self.model = YOLO(weights)
            self.device = device
            self.conf = conf
            self.iou = iou

        def track(self, frame_bgr, conf=None, iou=None):
            res = self.model.track(
                frame_bgr, persist=True,
                conf=conf if conf is not None else self.conf,
                iou=iou if iou is not None else self.iou,
                tracker="bytetrack.yaml",
                device=self.device,
                verbose=False,
            )[0]
            out = []
            if res.boxes is None or res.boxes.id is None:
                return out
            xyxy = res.boxes.xyxy.cpu().numpy().astype(int)
            conf_  = res.boxes.conf.cpu().numpy()
            cls  = res.boxes.cls.cpu().numpy().astype(int)
            ids  = res.boxes.id.cpu().numpy().astype(int)
            for b, c, k, tid in zip(xyxy, conf_, cls, ids):
                if k not in TARGET_CLASSES:
                    continue
                out.append(dict(bbox=b, conf=float(c), cls_id=int(k),
                                cls_name=TARGET_CLASSES[int(k)],
                                track_id=int(tid)))
            return out

    return (PersonObjectDetector,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 6 · Identity fusion

    Track IDs from ByteTrack are only short-term stable. For each track we
    keep a rolling window of per-frame identity guesses: **face matches
    count double**, ReID matches count single, unknowns count half. A track
    is *committed* to an employee when a clear winner accumulates
    enough weight.
    """)
    return


@app.cell
def _(Dict, Optional, Tuple, collections, np):
    UNKNOWN_LABEL = "UNKNOWN"

    def cosine_matrix(a, b):
        if a.size == 0 or b.size == 0:
            return np.zeros((a.shape[0] if a.ndim == 2 else 0,
                             b.shape[0] if b.ndim == 2 else 0), np.float32)
        return a @ b.T

    class IdentityFuser:
        def __init__(self, gallery, face_emb, reid_emb,
                     face_thr=0.45, reid_thr=0.75, window=30):
            self.gallery = gallery
            self.face_emb = face_emb
            self.reid_emb = reid_emb
            self.face_thr = face_thr
            self.reid_thr = reid_thr
            self.window = window
            self.votes: Dict[int, collections.deque] = collections.defaultdict(
                lambda: collections.deque(maxlen=window)
            )
            self.committed: Dict[int, str] = {}

        def _match_face(self, crop) -> Optional[Tuple[str, float]]:
            v = self.face_emb.embed(crop)
            if v is None:
                return None
            sims = cosine_matrix(v[None, :], self.gallery.face_vecs)[0]
            i = int(np.argmax(sims)); s = float(sims[i])
            return (self.gallery.names[i], s) if s >= self.face_thr else None

        def _match_reid(self, crop) -> Optional[Tuple[str, float]]:
            v = self.reid_emb.embed(crop)
            best_name, best_s = None, -1.0
            for name, bank in self.gallery.reid_banks.items():
                if bank.size == 0:
                    continue
                s = float(np.max(bank @ v))
                if s > best_s:
                    best_name, best_s = name, s
            if best_name is not None and best_s >= self.reid_thr:
                return best_name, best_s
            return None

        def update(self, track_id: int, crop) -> str:
            if track_id in self.committed:
                return self.committed[track_id]
            guess = self._match_face(crop)
            if guess is not None:
                self.votes[track_id].append((guess[0], 2.0))
            else:
                guess = self._match_reid(crop)
                if guess is not None:
                    self.votes[track_id].append((guess[0], 1.0))
                else:
                    self.votes[track_id].append((UNKNOWN_LABEL, 0.5))
            tally = collections.Counter()
            for name, w in self.votes[track_id]:
                tally[name] += w
            winner, score = tally.most_common(1)[0]
            total = sum(tally.values())
            if winner != UNKNOWN_LABEL and score / total > 0.55 and score >= 5:
                self.committed[track_id] = winner
            return winner

    return IdentityFuser, UNKNOWN_LABEL


@app.cell
def _(mo):
    mo.md(r"""
    ## 7 · Activity event engine
    Three detectors run every frame:

    * **Presence** — employee's foot-point inside a desk polygon.
    * **Phone use** — phone-bbox overlaps person-bbox for ≥ `PHONE_MIN_SECS`.
    * **Interaction** — two identified employees within `prox_px` for
      ≥ `INTERACTION_MIN_SECS`.
    """)
    return


@app.cell
def _(Dict, List, Optional, UNKNOWN_LABEL, dataclasses, math, pd):
    from shapely.geometry import Polygon, Point, box as shapely_box

    @dataclasses.dataclass
    class Zone:
        name: str
        polygon: Polygon

        @classmethod
        def rect(cls, name, x1, y1, x2, y2):
            return cls(name, shapely_box(x1, y1, x2, y2))

        def contains_bbox(self, bbox):
            x1, y1, x2, y2 = bbox
            foot = Point((x1 + x2) / 2, y2 - 0.05 * (y2 - y1))
            return self.polygon.contains(foot)

    DEFAULT_ZONES = [
        Zone.rect("desk_A",   50, 200,  500, 650),
        Zone.rect("desk_B",  550, 200, 1000, 650),
        Zone.rect("common", 1020, 200, 1900, 1050),
    ]

    def build_zones_from_config(zones_config: List[tuple]) -> List["Zone"]:
        """Build Zone objects from configuration tuples."""
        if not zones_config:
            return DEFAULT_ZONES
        return [Zone.rect(name, x1, y1, x2, y2) for name, x1, y1, x2, y2 in zones_config]

    @dataclasses.dataclass
    class Event:
        employee_id: str
        event_type: str
        start_frame: int
        end_frame: Optional[int] = None
        start_ts: Optional[float] = None
        end_ts: Optional[float] = None
        details: dict = dataclasses.field(default_factory=dict)

        @property
        def duration_s(self):
            if self.start_ts is None or self.end_ts is None:
                return None
            return round(self.end_ts - self.start_ts, 2)

    class EventEngine:
        def __init__(self, fps, zones, grace_s=1.5,
                     proximity_px=180, phone_iou_thr=0.05,
                     work_device_iou_thr=0.02, work_device_overlap_thr=0.25,
                     presence_min_s=2, phone_min_s=2, working_min_s=3,
                     interaction_min_s=4):
            self.fps = fps
            self.zones = zones
            self.grace_frames = int(grace_s * fps)
            self.prox = proximity_px
            self.phone_iou = phone_iou_thr
            self.work_iou = work_device_iou_thr
            self.work_overlap = work_device_overlap_thr
            self.mins = {"presence": presence_min_s,
                         "phone_use": phone_min_s,
                         "working": working_min_s,
                         "interaction": interaction_min_s}
            self.open: Dict[str, Event] = {}
            self.last_seen: Dict[str, int] = {}
            self.all: List[Event] = []

        def _key(self, kind, *parts):
            return kind + "::" + "::".join(str(p) for p in parts)

        def _start_or_refresh(self, key, kind, emp, fidx, details=None):
            if key not in self.open:
                self.open[key] = Event(
                    employee_id=emp, event_type=kind,
                    start_frame=fidx, start_ts=fidx / self.fps,
                    details=details or {},
                )
            self.last_seen[key] = fidx

        def _close_stale(self, fidx):
            for key in list(self.open.keys()):
                if fidx - self.last_seen[key] > self.grace_frames:
                    ev = self.open.pop(key)
                    ev.end_frame = self.last_seen[key]
                    ev.end_ts = ev.end_frame / self.fps
                    self.all.append(ev)
                    del self.last_seen[key]

        def update(self, fidx, people, phones, laptops, monitors):
            def _bbox_inter_metrics(a, b):
                ax1, ay1, ax2, ay2 = a
                bx1, by1, bx2, by2 = b
                ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                ix2, iy2 = min(ax2, bx2), min(ay2, by2)
                if ix2 <= ix1 or iy2 <= iy1:
                    return 0.0, 0.0
                inter = float((ix2 - ix1) * (iy2 - iy1))
                a_area = float(max(0, ax2 - ax1) * max(0, ay2 - ay1))
                b_area = float(max(0, bx2 - bx1) * max(0, by2 - by1))
                iou = inter / (a_area + b_area - inter + 1e-9)
                overlap_on_b = inter / (b_area + 1e-9)
                return iou, overlap_on_b

            # presence: choose one containing zone, prefer desk_* zones
            for p in people:
                containing = [z for z in self.zones if z.contains_bbox(p["bbox"])]
                if not containing:
                    continue
                desk_containing = [z for z in containing if z.name.lower().startswith("desk")]
                chosen = (sorted(desk_containing, key=lambda z: z.polygon.area)[0]
                          if desk_containing
                          else sorted(containing, key=lambda z: z.polygon.area)[0])
                k = self._key("presence", p["employee_id"], chosen.name)
                self._start_or_refresh(k, "presence", p["employee_id"],
                                       fidx, {"zone": chosen.name})
            # phone use
            for p in people:
                pb = p["bbox"]
                for ph in phones:
                    iou, overlap_on_phone = _bbox_inter_metrics(pb, ph["bbox"])
                    if iou >= self.phone_iou or overlap_on_phone > 0.5:
                        k = self._key("phone_use", p["employee_id"])
                        self._start_or_refresh(k, "phone_use",
                                               p["employee_id"], fidx)
            # office working proxy: in desk zone + nearby laptop + monitor
            desk_zones = [z for z in self.zones if z.name.lower().startswith("desk")]
            for p in people:
                if p["employee_id"] == UNKNOWN_LABEL:
                    continue
                pb = p["bbox"]
                for z in desk_zones:
                    if not z.contains_bbox(pb):
                        continue
                    has_laptop = any(
                        (_bbox_inter_metrics(pb, d["bbox"])[0] >= self.work_iou) or
                        (_bbox_inter_metrics(pb, d["bbox"])[1] >= self.work_overlap)
                        for d in laptops
                    )
                    has_monitor = any(
                        (_bbox_inter_metrics(pb, d["bbox"])[0] >= self.work_iou) or
                        (_bbox_inter_metrics(pb, d["bbox"])[1] >= self.work_overlap)
                        for d in monitors
                    )
                    if has_laptop and has_monitor:
                        k = self._key("working", p["employee_id"], z.name)
                        self._start_or_refresh(
                            k, "working", p["employee_id"], fidx,
                            {"zone": z.name, "work_proxy": "laptop+monitor"},
                        )
            # interactions
            ids = [p for p in people if p["employee_id"] != UNKNOWN_LABEL]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    if a["employee_id"] == b["employee_id"]:
                        continue
                    ca = ((a["bbox"][0] + a["bbox"][2]) / 2,
                          (a["bbox"][1] + a["bbox"][3]) / 2)
                    cb = ((b["bbox"][0] + b["bbox"][2]) / 2,
                          (b["bbox"][1] + b["bbox"][3]) / 2)
                    dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
                    if dist <= self.prox:
                        pair = tuple(sorted([a["employee_id"],
                                             b["employee_id"]]))
                        k = self._key("interaction", *pair)
                        self._start_or_refresh(
                            k, "interaction",
                            f"{pair[0]} <-> {pair[1]}", fidx,
                            {"peers": list(pair), "dist": round(dist, 1)},
                        )
            self._close_stale(fidx)

        def flush(self, fidx):
            for key, ev in list(self.open.items()):
                ev.end_frame = self.last_seen.get(key, fidx)
                ev.end_ts = ev.end_frame / self.fps
                self.all.append(ev)
            self.open.clear()
            self.last_seen.clear()

        def to_dataframe(self) -> pd.DataFrame:
            rows = []
            for ev in self.all:
                if ev.duration_s is None:
                    continue
                if ev.duration_s < self.mins.get(ev.event_type, 0):
                    continue
                rows.append({
                    "employee_id": ev.employee_id,
                    "event_type":  ev.event_type,
                    "start_s":     round(ev.start_ts, 2),
                    "end_s":       round(ev.end_ts, 2),
                    "duration_s":  ev.duration_s,
                    **ev.details,
                })
            if not rows:
                return pd.DataFrame(columns=["employee_id", "event_type",
                                             "start_s", "end_s", "duration_s"])
            return (pd.DataFrame(rows)
                    .sort_values(["start_s", "employee_id"])
                    .reset_index(drop=True))

    return EventEngine, build_zones_from_config


@app.cell
def _(mo):
    mo.md("""
    ## 8 · End-to-end pipeline
    """)
    return


@app.cell
def _(cv2, np):
    COLORS = np.random.randint(0, 255, size=(128, 3), dtype=np.uint8)

    def _color_for(name: str):
        h = sum(ord(c) for c in name) % 128
        return tuple(int(v) for v in COLORS[h])

    def draw_overlay(frame, people, phones, laptops, monitors, zones, fidx, fps, ticker):
        vis = frame.copy()
        for z in zones:
            pts = np.array(z.polygon.exterior.coords, np.int32)
            cv2.polylines(vis, [pts], True, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(vis, z.name, tuple(pts[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200),
                        1, cv2.LINE_AA)
        for ph in phones:
            x1, y1, x2, y2 = ph["bbox"]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 180, 255), 2)
            cv2.putText(vis, "phone", (x1, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 180, 255),
                        1, cv2.LINE_AA)
        for lp in laptops:
            x1, y1, x2, y2 = lp["bbox"]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (80, 210, 80), 2)
            cv2.putText(vis, "laptop", (x1, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 210, 80),
                        1, cv2.LINE_AA)
        for mo in monitors:
            x1, y1, x2, y2 = mo["bbox"]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (220, 120, 30), 2)
            cv2.putText(vis, "monitor", (x1, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 120, 30),
                        1, cv2.LINE_AA)
        for p in people:
            x1, y1, x2, y2 = p["bbox"]
            col = _color_for(p["employee_id"])
            cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2)
            cv2.putText(vis, f"{p['employee_id']} #{p['track_id']}",
                        (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, col, 2, cv2.LINE_AA)
        t = fidx / max(fps, 1e-6)
        cv2.putText(vis, f"t={t:6.2f}s  frame={fidx}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255),
                    2, cv2.LINE_AA)
        for i, ev in enumerate(ticker[-3:]):
            cv2.putText(vis, f"• {ev}", (10, 55 + 20 * i),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 180),
                        1, cv2.LINE_AA)
        return vis

    return (draw_overlay,)


@app.cell
def _(mo):
    run_btn = mo.ui.run_button(label="▶ Run pipeline on selected video", kind="success")
    run_btn
    return (run_btn,)


@app.cell
def _(build_zones_from_config, zones_parsed):
    """Build Zone objects from the current configuration."""
    active_zones = build_zones_from_config(zones_parsed)
    return (active_zones,)


@app.cell
def _(
    EventEngine,
    IdentityFuser,
    OUT_DIR,
    Path,
    PersonObjectDetector,
    UNKNOWN_LABEL,
    active_zones,
    cv2,
    det_conf,
    det_iou,
    draw_overlay,
    face_emb,
    face_thr,
    fuse_win,
    gallery,
    max_frames,
    mo,
    pd,
    prox_px,
    reid_emb,
    reid_thr,
    run_btn,
    stride,
    video_picker,
):
    """Heavy cell — only runs when the user clicks Run."""
    events_df = pd.DataFrame()
    annotated_path = None
    run_summary = mo.md("*Click ▶ above to run the pipeline on the selected video.*")

    if run_btn.value:
        _video_path = Path(video_picker.value)
        _detector = PersonObjectDetector(conf=det_conf.value, iou=det_iou.value)
        _fuser = None
        if gallery is not None:
            _fuser = IdentityFuser(gallery, face_emb, reid_emb,
                                   face_thr=face_thr.value,
                                   reid_thr=reid_thr.value,
                                   window=fuse_win.value)

        _cap = cv2.VideoCapture(str(_video_path))
        _fps = _cap.get(cv2.CAP_PROP_FPS) or 25.0
        _W = int(_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        _H = int(_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        annotated_path = OUT_DIR / f"{_video_path.stem}_annotated.mp4"
        _writer = cv2.VideoWriter(str(annotated_path),
                                  cv2.VideoWriter_fourcc(*"mp4v"), 15,
                                  (_W, _H))

        _engine = EventEngine(
            fps=_fps / stride.value,
            zones=active_zones,
            proximity_px=prox_px.value,
        )
        _ticker = []
        _idx = _processed = 0
        _limit = max_frames.value
        while _processed < _limit:
            _ok, _frame = _cap.read()
            if not _ok:
                break
            if _idx % stride.value != 0:
                _idx += 1
                continue
            _dets = _detector.track(_frame)
            _people, _phones, _laptops, _monitors = [], [], [], []
            for _d in _dets:
                if _d["cls_name"] == "person":
                    _x1, _y1, _x2, _y2 = _d["bbox"]
                    _x1, _y1 = max(0, _x1), max(0, _y1)
                    _x2, _y2 = min(_W, _x2), min(_H, _y2)
                    _crop = _frame[_y1:_y2, _x1:_x2]
                    _name = UNKNOWN_LABEL
                    if _fuser is not None and _crop.size > 0:
                        _name = _fuser.update(_d["track_id"], _crop)
                    _people.append({**_d, "employee_id": _name})
                elif _d["cls_name"] == "cell phone":
                    _phones.append(_d)
                elif _d["cls_name"] == "laptop":
                    _laptops.append(_d)
                elif _d["cls_name"] == "monitor":
                    _monitors.append(_d)
            _prev = len(_engine.all)
            _engine.update(_processed, _people, _phones, _laptops, _monitors)
            for _ev in _engine.all[_prev:]:
                _ticker.append(
                    f"{_ev.event_type}: {_ev.employee_id} @ {_ev.start_ts:.1f}s"
                )
            _vis = draw_overlay(_frame, _people, _phones, _laptops, _monitors, active_zones,
                                _processed, _fps / stride.value, _ticker)
            _writer.write(_vis)
            _idx += 1
            _processed += 1

        _cap.release(); _writer.release()
        _engine.flush(_processed)
        events_df = _engine.to_dataframe()
        events_df.to_csv(OUT_DIR / f"{_video_path.stem}_events.csv", index=False)

        run_summary = mo.md(
            f"✓ Processed **{_processed}** frames of `{_video_path.name}` "
            f"(stride={stride.value}) · emitted **{len(events_df)}** events · "
            f"annotated video: `{annotated_path.name}`"
        )

    run_summary
    return (events_df,)


@app.cell
def _(mo):
    mo.md("""
    ## 9 · Event explorer
    """)
    return


@app.cell
def _(events_df, mo):
    if events_df.empty:
        emp_filter = mo.ui.multiselect(options=[], value=[], label="Employee filter")
        type_filter = mo.ui.multiselect(options=[], value=[], label="Event type filter")
    else:
        emps  = sorted(events_df["employee_id"].unique().tolist())
        types = sorted(events_df["event_type"].unique().tolist())
        emp_filter  = mo.ui.multiselect(options=emps,  value=emps,  label="Employee filter")
        type_filter = mo.ui.multiselect(options=types, value=types, label="Event type filter")

    mo.hstack([emp_filter, type_filter], justify="start")
    return emp_filter, type_filter


@app.cell
def _(emp_filter, events_df, mo, type_filter):
    if events_df.empty:
        filtered = events_df
    else:
        filtered = events_df[
            events_df["employee_id"].isin(emp_filter.value)
            & events_df["event_type"].isin(type_filter.value)
        ].reset_index(drop=True)
    mo.ui.table(filtered, page_size=15, label=f"{len(filtered)} events")
    return (filtered,)


@app.cell
def _(filtered, plt, sns):
    def _timeline(df):
        if df.empty:
            return None
        types = df["event_type"].unique().tolist()
        palette = dict(zip(types, sns.color_palette("tab10", n_colors=len(types))))
        emps = df["employee_id"].unique().tolist()
        y = {e: i for i, e in enumerate(emps)}
        fig, ax = plt.subplots(figsize=(10, 0.55 * len(emps) + 1.5))
        for _, r in df.iterrows():
            ax.barh(y[r["employee_id"]], width=r["duration_s"], left=r["start_s"],
                    height=0.55, color=palette[r["event_type"]],
                    edgecolor="black", lw=0.2)
        handles = [plt.Rectangle((0, 0), 1, 1, color=palette[t]) for t in types]
        ax.legend(handles, types, loc="upper right", ncol=len(types), fontsize=8)
        ax.set_yticks(list(y.values())); ax.set_yticklabels(list(y.keys()))
        ax.set_xlabel("time (s)"); ax.set_title("Activity timeline")
        plt.tight_layout()
        return fig

    _timeline(filtered)
    return


@app.cell
def _(filtered, plt, sns):
    def _totals(df):
        if df.empty:
            return None
        totals = (df.groupby(["employee_id", "event_type"])["duration_s"]
                    .sum().reset_index())
        fig, ax = plt.subplots(figsize=(8, 3.5))
        sns.barplot(data=totals, x="employee_id", y="duration_s",
                    hue="event_type", ax=ax)
        ax.set_ylabel("seconds"); ax.set_title("Total activity per employee")
        plt.tight_layout()
        return fig

    _totals(filtered)
    return


@app.cell
def _(filtered, mo, plt):
    def _graph(df):
        inter = df[df["event_type"] == "interaction"]
        if inter.empty:
            return mo.md("*(No interaction events in the current filter.)*")
        try:
            import networkx as nx
        except ImportError:
            return mo.md("`pip install networkx` for the interaction graph.")
        G = nx.Graph()
        for _, r in inter.iterrows():
            peers = r.get("peers")
            if isinstance(peers, (list, tuple)) and len(peers) == 2:
                a, b = peers
            else:
                parts = str(r["employee_id"]).split(" <-> ")
                if len(parts) != 2:
                    continue
                a, b = parts
            w = r["duration_s"]
            if G.has_edge(a, b):
                G[a][b]["weight"] += w
            else:
                G.add_edge(a, b, weight=w)
        fig, ax = plt.subplots(figsize=(6, 4))
        pos = nx.spring_layout(G, seed=42)
        widths = [G[u][v]["weight"] / 10 for u, v in G.edges()]
        nx.draw(G, pos, with_labels=True, node_color="#4dabf7",
                node_size=1300, width=widths, edge_color="#555", ax=ax)
        ax.set_title("Interaction graph (edge width ∝ total seconds)")
        plt.tight_layout()
        return fig

    _graph(filtered)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 10 · Notes

    * The heavy "Run pipeline" cell is **click-gated** — marimo would
      otherwise re-run it whenever *any* slider changed. Tune thresholds
      first, then click **▶ Run pipeline**.
    * The event explorer plots below the table are fully reactive — drag a
      slider or toggle a filter chip and they redraw.
    * Running as an app (`marimo run …`) exposes the notebook as a small
      web UI with no code visible to the end user — useful for a demo.

    Same caveats as the Jupyter notebook: inform employees, collect
    consent, short retention windows, restricted access. This is a
    research prototype.
    """)
    return


if __name__ == "__main__":
    app.run()
