"""几何判据共享同一实现，station 可以是标量点，也可以是 (x, y) 数组。"""
from dataclasses import dataclass
from math import radians, tan

import numpy as np

from .config import (
    BODY_TOL_M, BUILDINGS, CAMERA_HEIGHT, DISTANCE_TOL_M,
    ELEVATION_LIMITS_DEG, FIELD_SIZE, VEHICLE_RADIUS,
)

SEGMENT_T_TOL = 16 * np.finfo(float).eps  # 线段参数，无量纲


def building_clearance(station):
    """车心到各建筑占地的最小距离减车体半径。外切记录为零。"""
    x, y = station
    nearest = np.inf
    for left, right, bottom, top, _ in BUILDINGS:
        dx = np.maximum(0, np.maximum(left - x, x - right))
        dy = np.maximum(0, np.maximum(bottom - y, y - top))
        nearest = np.minimum(nearest, np.hypot(dx, dy))
    gap = nearest - VEHICLE_RADIUS
    return np.where(np.abs(gap) <= BODY_TOL_M, 0.0, gap)


def intersects_building(target, station, building):
    """用三个闭区间裁剪三维视线段，包括只接触面、棱或顶点的情形。"""
    x, y = station
    tx, ty, tz = target
    left, right, bottom, top, height = building
    enter, leave = 0.0, 1.0
    overlaps = True
    for start, end, low, high in (
        (x, tx, left, right), (y, ty, bottom, top), (CAMERA_HEIGHT, tz, 0, height),
    ):
        delta = end - start
        parallel = np.equal(delta, 0)
        overlaps = overlaps & (~parallel | ((low <= start) & (start <= high)))
        divisor = np.where(parallel, 1.0, delta)
        t1, t2 = (low - start) / divisor, (high - start) / divisor
        enter = np.maximum(enter, np.where(parallel, -np.inf, np.minimum(t1, t2)))
        leave = np.minimum(leave, np.where(parallel, np.inf, np.maximum(t1, t2)))
    return overlaps & (enter <= leave + SEGMENT_T_TOL)


def minimum_elevation_range(target):
    """仰角允许区间跨越 0°。返回这一条件对水平距离给出的下界（m）。"""
    dz = target[2] - CAMERA_HEIGHT
    limiting_angle = ELEVATION_LIMITS_DEG[1] if dz >= 0 else ELEVATION_LIMITS_DEG[0]
    return dz / tan(radians(limiting_angle))


@dataclass
class StationCheck:
    in_field: np.ndarray
    clearance_m: np.ndarray
    elevation_ok: np.ndarray
    sight_clear: np.ndarray
    distance_m: np.ndarray
    elevation_deg: np.ndarray

    @property
    def observable(self):
        return self.in_field & (self.clearance_m >= 0) & self.elevation_ok & self.sight_clear


def check_station(target, station):
    x, y = (np.asarray(axis, dtype=float) for axis in station)
    r = np.hypot(target[0] - x, target[1] - y)
    dz = target[2] - CAMERA_HEIGHT
    in_field = ((VEHICLE_RADIUS <= x) & (x <= FIELD_SIZE - VEHICLE_RADIUS)
                & (VEHICLE_RADIUS <= y) & (y <= FIELD_SIZE - VEHICLE_RADIUS))
    clearance = building_clearance((x, y))
    elevation = np.rad2deg(np.arctan2(dz, r))
    elevation_ok = (r >= minimum_elevation_range(target) - DISTANCE_TOL_M) & (r > 0)
    clear = np.ones_like(r, dtype=bool)
    for building in BUILDINGS:
        clear &= ~intersects_building(target, (x, y), building)
    return StationCheck(in_field, clearance, elevation_ok, clear, r, elevation)


def is_station_observable(target, station):
    return check_station(target, station).observable
