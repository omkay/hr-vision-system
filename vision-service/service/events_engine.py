"""Rule-based event detection: presence, phone use, working, interactions."""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from shapely.geometry import Polygon, Point, box as shapely_box

from .config import ZONES_CONFIG_PATH
from .identity import UNKNOWN_LABEL


@dataclasses.dataclass
class Zone:
    name: str
    polygon: Polygon
    zone_type: str = "work_area"   # "work_area" | "common_area"

    @classmethod
    def rect(cls, name, x1, y1, x2, y2, zone_type: str = "work_area"):
        return cls(name, shapely_box(x1, y1, x2, y2), zone_type)

    def contains_bbox(self, bbox):
        x1, y1, x2, y2 = bbox
        foot = Point((x1 + x2) / 2, y2 - 0.05 * (y2 - y1))
        return self.polygon.contains(foot)


def full_frame_zone(W: int, H: int, label: str = "work_area",
                    zone_type: str = "work_area") -> List[Zone]:
    """Single zone covering the entire video frame."""
    return [Zone.rect(label, 0, 0, W, H, zone_type=zone_type)]


def build_zones_from_config(zones_config: List[tuple]) -> List[Zone]:
    """Build Zone objects from a list of (name, x1, y1, x2, y2) tuples.

    Used when loading from zones_config.json (no zone_type info available,
    so all zones default to 'work_area').
    """
    return [Zone.rect(name, x1, y1, x2, y2) for name, x1, y1, x2, y2 in zones_config]


def load_zones_for_video(
    video_name: str,
    frame_size: Optional[tuple] = None,
) -> List[Zone]:
    """Return zones for *video_name*.

    Priority:
    1. Per-video entry in zones_config.json  (zone_type defaults to 'work_area')
    2. Full-frame 'work_area' zone when frame_size=(W, H) is supplied.
    """
    if ZONES_CONFIG_PATH.exists():
        data = json.loads(ZONES_CONFIG_PATH.read_text())
        raw = data.get(video_name, [])
        if raw:
            return build_zones_from_config([tuple(z) for z in raw])
    if frame_size is not None:
        W, H = frame_size
        return full_frame_zone(W, H)
    # Hard-coded last resort — should rarely be reached in practice.
    return [
        Zone.rect("desk_A",   50, 200,  500, 650, zone_type="work_area"),
        Zone.rect("desk_B",  550, 200, 1000, 650, zone_type="work_area"),
        Zone.rect("common", 1020, 200, 1900, 1050, zone_type="common_area"),
    ]


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
        self.mins = {"presence": presence_min_s, "phone_use": phone_min_s,
                     "working": working_min_s, "interaction": interaction_min_s}
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

    @staticmethod
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

    def update(self, fidx, people, phones, laptops, monitors):
        # ── Presence ────────────────────────────────────────────────────────
        for p in people:
            containing = [z for z in self.zones if z.contains_bbox(p["bbox"])]
            if not containing:
                continue
            # Prefer work_area zones; fall back to any zone. Break ties by area (smallest first).
            work_containing = [z for z in containing if z.zone_type == "work_area"]
            chosen = sorted(work_containing or containing, key=lambda z: z.polygon.area)[0]
            k = self._key("presence", p["employee_id"], chosen.name)
            self._start_or_refresh(k, "presence", p["employee_id"], fidx,
                                   {"zone": chosen.name, "zone_type": chosen.zone_type})

        # ── Phone use ────────────────────────────────────────────────────────
        for p in people:
            pb = p["bbox"]
            for ph in phones:
                iou, overlap_on_phone = self._bbox_inter_metrics(pb, ph["bbox"])
                if iou >= self.phone_iou or overlap_on_phone > 0.5:
                    k = self._key("phone_use", p["employee_id"])
                    self._start_or_refresh(k, "phone_use", p["employee_id"], fidx)

        # ── Working (laptop + monitor in a work_area zone) ───────────────────
        work_zones = [z for z in self.zones if z.zone_type == "work_area"]
        for p in people:
            if p["employee_id"] == UNKNOWN_LABEL:
                continue
            pb = p["bbox"]
            for z in work_zones:
                if not z.contains_bbox(pb):
                    continue
                has_laptop = any(
                    (self._bbox_inter_metrics(pb, d["bbox"])[0] >= self.work_iou) or
                    (self._bbox_inter_metrics(pb, d["bbox"])[1] >= self.work_overlap)
                    for d in laptops
                )
                has_monitor = any(
                    (self._bbox_inter_metrics(pb, d["bbox"])[0] >= self.work_iou) or
                    (self._bbox_inter_metrics(pb, d["bbox"])[1] >= self.work_overlap)
                    for d in monitors
                )
                if has_laptop and has_monitor:
                    k = self._key("working", p["employee_id"], z.name)
                    self._start_or_refresh(
                        k, "working", p["employee_id"], fidx,
                        {"zone": z.name, "zone_type": z.zone_type, "work_proxy": "laptop+monitor"},
                    )
        ids = [p for p in people if p["employee_id"] != UNKNOWN_LABEL]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if a["employee_id"] == b["employee_id"]:
                    continue
                ca = ((a["bbox"][0] + a["bbox"][2]) / 2, (a["bbox"][1] + a["bbox"][3]) / 2)
                cb = ((b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2)
                dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
                if dist <= self.prox:
                    pair = tuple(sorted([a["employee_id"], b["employee_id"]]))
                    k = self._key("interaction", *pair)
                    self._start_or_refresh(
                        k, "interaction", f"{pair[0]} <-> {pair[1]}", fidx,
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
