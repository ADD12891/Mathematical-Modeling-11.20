"""第三问：在真实水平距离固定的条件下，计算两种包角事件的概率。"""
from functools import lru_cache
from math import ceil, erf, erfc, exp, hypot, isfinite, pi, radians, sqrt, tan
from operator import index

import numpy as np
from numpy.polynomial.hermite import hermgauss
from numpy.polynomial.legendre import leggauss

from .question3_config import (
    Q3_BAND_DEG, Q3_CONTROL_STD_DEG, Q3_RANGE_M, Q3_REQUIRED_PROB, Q3_ZERO_STD_DEG,
)

SIGMA_BRACKET_TOL_M = 1e-11
# 题给 sigma(80)=1.9 m，仍使用原求积。更大位置误差使窄达标带在笛卡尔
# 积分网格下难以分辨，改积分方位密度。80 阶在切换点的概率偏差小于 1e-9。
ANGULAR_INTEGRATION_ABOVE_M = Q3_RANGE_M / 20
SQRT2 = sqrt(2.)
ERF = np.vectorize(erf, otypes=[float])
ERFC = np.vectorize(erfc, otypes=[float])


def _check_sigma(sigma):
    if not isfinite(sigma) or sigma < 0:
        raise ValueError("单坐标位置误差标准差须为非负有限值（m）")


class BearingProbability:
    """同一阶数的积分节点供多次试算及二分求根使用。函数的输入均为标准差（m）。"""

    def __init__(self, order):
        self.order = index(order)
        if self.order < 2:
            raise ValueError("求积阶数须为至少 2 的整数")
        nodes, weights = hermgauss(self.order)
        if not np.all(np.isfinite(weights)):
            raise ValueError("所选阶数的 Gauss–Hermite 权重超出浮点精度范围")
        self.z = SQRT2 * nodes
        self.weights = weights / sqrt(pi)
        self.weights_xy = np.outer(self.weights, self.weights)
        self.angle_nodes, self.angle_weights = leggauss(max(24, self.order))
        self.gimbal_sd_deg = hypot(Q3_ZERO_STD_DEG, Q3_CONTROL_STD_DEG)

    def _wrapped_band(self, angle_deg):
        """给定几何角差，积分独立云台误差落入各个 360° 周期达标带的概率。"""
        if self.gimbal_sd_deg == 0:
            return np.abs((angle_deg + 180.) % 360. - 180.) <= Q3_BAND_DEG
        # angle_deg 已在 [-180,180]。取过 12 个标准差的周期边界，漏计尾概率 < 4e-33。
        images = ceil((180. + Q3_BAND_DEG + 12*self.gimbal_sd_deg) / 360.)
        scale = SQRT2 * self.gimbal_sd_deg
        result = np.zeros_like(angle_deg, dtype=float)
        for k in range(-images, images + 1):
            offset = np.abs(angle_deg - 360.*k)
            result += 0.5 * (ERFC((offset - Q3_BAND_DEG) / scale)
                             - ERFC((offset + Q3_BAND_DEG) / scale))
        return result

    def _angle_integral(self, sigma, with_gimbal):
        """大位置误差时，先沿半径解析积分，再在方位维积分（密度按弧度）。"""
        ratio = Q3_RANGE_M / sigma
        gimbal_tail_deg = min(180., Q3_BAND_DEG + 12*self.gimbal_sd_deg)
        edges = [0., Q3_BAND_DEG]
        if with_gimbal and gimbal_tail_deg > Q3_BAND_DEG:
            edges.append(gimbal_tail_deg)
        probability = 0.
        for lower, upper in zip(edges, edges[1:]):
            half = radians(upper - lower) / 2
            angle = radians(lower) + half * (1 + self.angle_nodes)
            # 平面正态 N((R,0),sigma² I) 变到极坐标后，沿 rho >= 0 对 rho*f 积分。
            projection = ratio * np.cos(angle)
            density = (exp(-0.5 * ratio**2) / (2*pi)
                       + projection * np.exp(-0.5 * (ratio*np.sin(angle))**2)
                       * ERFC(-projection / SQRT2) / (2*sqrt(2*pi)))
            if with_gimbal:
                density *= self._wrapped_band(np.rad2deg(angle))
            probability += 2 * half * (self.angle_weights @ density)
        return float(np.clip(probability, 0., 1.))

    def command(self, sigma):
        """P(|Δψ_cmd|≤5°)。条件于纵向位置误差，对横向正态量解析积分。"""
        _check_sigma(sigma)
        if sigma == 0:
            return 1.
        if sigma > ANGULAR_INTEGRATION_ABOVE_M:
            return self._angle_integral(sigma, with_gimbal=False)
        forward = Q3_RANGE_M - sigma*self.z
        width = np.maximum(0., forward) * tan(radians(Q3_BAND_DEG))
        with np.errstate(over="ignore"):
            success = ERF(width / (SQRT2 * sigma))
        return float(np.clip(self.weights @ success, 0., 1.))

    def actual(self, sigma):
        """P(|Δψ_act|≤5°)。给定位置误差后，零偏与控制误差的方差相加。"""
        _check_sigma(sigma)
        if self.gimbal_sd_deg == 0:
            return self.command(sigma)
        if sigma == 0:
            return float(self._wrapped_band(0.))
        if sigma > ANGULAR_INTEGRATION_ABOVE_M:
            return self._angle_integral(sigma, with_gimbal=True)
        angle = np.rad2deg(np.arctan2(-sigma*self.z[:, None], Q3_RANGE_M - sigma*self.z))
        return float(np.clip(np.sum(self.weights_xy * self._wrapped_band(angle)), 0., 1.))


@lru_cache(maxsize=8)
def probability_model(order):
    return BearingProbability(order)


def critical_sigma(fn):
    """对单调下降的达标概率二分，返回 (达标下端, 未达标上端)，单位 m。"""
    lo, hi = 0., Q3_RANGE_M / 20
    if fn(lo) < Q3_REQUIRED_PROB:
        raise ValueError("零位置误差时也无法达到给定概率")
    while fn(hi) >= Q3_REQUIRED_PROB:
        lo, hi = hi, 2*hi
        if not isfinite(hi):
            raise ValueError("没有找到未达标的有限上端")
    while hi - lo > SIGMA_BRACKET_TOL_M:
        mid = lo + (hi - lo)/2
        if mid == lo or mid == hi:
            break
        if fn(mid) >= Q3_REQUIRED_PROB:
            lo = mid
        else:
            hi = mid
    return lo, hi
