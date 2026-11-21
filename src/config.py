"""第一、二问的标称输入。长度用 m，时间用 s，题给角度用度。"""
from math import cos, radians, sin

FIELD_SIZE = 100.0
CENTER = (50.0, 50.0)
VEHICLE_RADIUS = 1.5
CAMERA_HEIGHT = 2.0
ELEVATION_LIMITS_DEG = (-10.0, 80.0)
T1_ALERT = (30.0, 40.0, 45.0)  # 方位角、水平距离、高度
ALERTS = {"T1": T1_ALERT, "T2": (150.0, 45.0, 35.0), "T3": (285.0, 38.0, 55.0)}
VEHICLES = {"V1": (15.0, 14.0), "V2": (88.0, 10.0), "V3": (14.0, 86.0),
            "V4": (88.0, 68.0), "V5": (50.0, 93.0)}
SPEED = 3.0
SLEW_RATE_DEG_S = 30.0
OBSERVATION_S = 5.0
TRAVEL_LIMIT_S = 30.0
BEARING_STD_DEG = 1.0

# B1 到 B5，各行为 xmin、xmax、ymin、ymax、高度。
BUILDINGS = (
    (22, 34, 16, 26, 20),
    (63, 79, 59, 71, 28),
    (10, 24, 65, 76, 12),
    (72, 86, 22, 34, 24),
    (41, 51, 38, 46, 32),
)
GRID_STEPS = (0.5, 0.25, 0.125)
BODY_TOL_M = 1e-12
DISTANCE_TOL_M = 1e-9


def target_position(alert):
    bearing, distance, height = alert
    theta = radians(bearing)
    return CENTER[0] + distance * cos(theta), CENTER[1] + distance * sin(theta), height


def t1_position():
    return target_position(T1_ALERT)
