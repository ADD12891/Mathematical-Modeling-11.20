"""圆盘车辆的静态路线。折线长度是连续最短路的可行上界。

直线可通时返回精确最短路。受阻时，在圆角的外接切线交点上建可见图，
并求该图中的最短路。增大 corner_steps 可加密各个四分之一圆上的切线。
边的通行性始终按原始建筑与整段车心轨迹计算。
"""
from heapq import heappop, heappush
from math import cos, dist, hypot, inf, pi, sin

from .config import BODY_TOL_M, BUILDINGS, FIELD_SIZE, VEHICLE_RADIUS


def route_length(path):
    return sum(dist(a, b) for a, b in zip(path, path[1:]))


def _footprint_distance(start, end, building):
    """线段到闭矩形的欧氏距离，包含退化为点的线段。"""
    left, right, bottom, top, _ = building
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    enter, leave = 0., 1.
    for origin, delta, low, high in ((ax, dx, left, right), (ay, dy, bottom, top)):
        if delta == 0:
            if low <= origin <= high:
                continue
            break
        t1, t2 = (low - origin) / delta, (high - origin) / delta
        enter = max(enter, min(t1, t2))
        leave = min(leave, max(t1, t2))
        if enter > leave:
            break
    else:
        return 0.

    def endpoint_distance(x, y):
        return hypot(max(left - x, 0., x - right), max(bottom - y, 0., y - top))

    # 分离的两凸多边形边段，其最近点至少有一个是端点或矩形顶点。
    gap = min(endpoint_distance(ax, ay), endpoint_distance(bx, by))
    length_sq = dx*dx + dy*dy
    if length_sq:
        for x in (left, right):
            for y in (bottom, top):
                t = ((x - ax)*dx + (y - ay)*dy) / length_sq
                if 0. < t < 1.:
                    gap = min(gap, hypot(ax + t*dx - x, ay + t*dy - y))
    return gap


def segment_clearance(start, end):
    """整段车体到最近建筑的有符号净空，单位 m。负值表示侵入。"""
    gap = min(_footprint_distance(start, end, box) for box in BUILDINGS) - VEHICLE_RADIUS
    return 0. if abs(gap) <= BODY_TOL_M else gap


def _inside_field(point):
    return all(VEHICLE_RADIUS <= axis <= FIELD_SIZE - VEHICLE_RADIUS for axis in point)


def _visible(start, end):
    """调用前两个端点均已通过场界检查，凸场界内的整段也满足场界约束。"""
    xmin, xmax = sorted((start[0], end[0]))
    ymin, ymax = sorted((start[1], end[1]))
    for box in BUILDINGS:
        left, right, bottom, top, _ = box
        if (xmax < left - VEHICLE_RADIUS or xmin > right + VEHICLE_RADIUS
                or ymax < bottom - VEHICLE_RADIUS or ymin > top + VEHICLE_RADIUS):
            continue
        if _footprint_distance(start, end, box) < VEHICLE_RADIUS - BODY_TOL_M:
            return False
    return True


def _corner_nodes(steps):
    angle_step = pi / (2 * steps)
    radius = VEHICLE_RADIUS / cos(angle_step / 2)
    nodes = []
    for left, right, bottom, top, _ in BUILDINGS:
        corners = ((right, top), (left, top), (left, bottom), (right, bottom))
        for quadrant, (x, y) in enumerate(corners):
            for i in range(steps):
                theta = quadrant * pi / 2 + (i + .5) * angle_step
                point = (x + radius * cos(theta), y + radius * sin(theta))
                if _inside_field(point) and _visible(point, point):
                    nodes.append(point)
    return nodes


class Router:
    """固定场景的一组路线查询，共用可见图、端点连边、最短路树及已有路线。"""

    def __init__(self, corner_steps=4):
        if not isinstance(corner_steps, int) or corner_steps < 1:
            raise ValueError("corner_steps 应为正整数")
        self.corner_steps = corner_steps
        self.nodes = _corner_nodes(corner_steps)
        self.graph = [[] for _ in self.nodes]
        for i, a in enumerate(self.nodes):
            for j in range(i):
                b = self.nodes[j]
                if _visible(a, b):
                    weight = dist(a, b)
                    self.graph[i].append((j, weight))
                    self.graph[j].append((i, weight))
        self._connections = {}
        self._trees = {}
        self._paths = {}

    def _connect(self, endpoint):
        if endpoint not in self._connections:
            self._connections[endpoint] = [
                (i, dist(endpoint, node)) for i, node in enumerate(self.nodes)
                if _visible(endpoint, node)
            ]
        return self._connections[endpoint]

    def _tree(self, origin):
        if origin in self._trees:
            return self._trees[origin]
        distances = [inf] * len(self.nodes)
        parents = [None] * len(self.nodes)
        queue = []
        for i, length in self._connect(origin):
            distances[i] = length
            heappush(queue, (length, i))
        while queue:
            length, i = heappop(queue)
            if length > distances[i]:
                continue
            for j, weight in self.graph[i]:
                candidate = length + weight
                if candidate < distances[j]:
                    distances[j] = candidate
                    parents[j] = i
                    heappush(queue, (candidate, j))
        self._trees[origin] = distances, parents
        return distances, parents

    def _find_path(self, origin, destination):
        if not (_inside_field(origin) and _inside_field(destination)):
            return None
        if _visible(origin, destination):
            return (origin, destination)
        if not (_visible(origin, origin) and _visible(destination, destination)):
            return None
        distances, parents = self._tree(origin)
        length, i = min(((distances[i] + w, i) for i, w in self._connect(destination)),
                        default=(inf, None))
        if length == inf:
            return None
        reverse_path = [destination]
        while i is not None:
            if self.nodes[i] != reverse_path[-1]:
                reverse_path.append(self.nodes[i])
            i = parents[i]
        if reverse_path[-1] != origin:
            reverse_path.append(origin)
        return tuple(reversed(reverse_path))

    def route(self, start_xy, end_xy):
        """返回含起终点的可行折线；非法端点或可见图未找到路线时返回 None。"""
        start, end = tuple(map(float, start_xy)), tuple(map(float, end_xy))
        origin, destination = (start, end) if start <= end else (end, start)
        key = origin, destination
        if key not in self._paths:
            self._paths[key] = self._find_path(origin, destination)
        path = self._paths[key]
        return path[::-1] if path is not None and origin != start else path
