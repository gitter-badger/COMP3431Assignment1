#!/usr/bin/env python
# coding=utf-8

"""Search Path Service for ROS based on A* algorithm.

This service provides A* algorithm to compute the shortest path between two points in the OccupancyGrid map.
"""

__author__ = 'kelvin'

import math
import heapq

import rospy
from nav_msgs.msg import OccupancyGrid
from assignment1.msg import *

from assignment1.srv import *

from constants import robot_radius
from constants import max_map_size

occ_threshold = 60
safety_margin_percentage = 0.2
path_unknown_marker = -1
inf = float('inf')


def euclidean_distance(x1, y1, x2, y2):
    """Get the euclidean distance between point (x1, y1) and (x2, y2)."""
    dx = (x1 - x2)
    dy = (y1 - y2)
    return math.sqrt(dx * dx + dy * dy)


def map_distance(map_grid, augmented_occ, start, end):
    """Get the map distance between start point and end point.

    If the end point is visitable, it will return Infinity, otherwise return the euclidean distance.
    @type map_grid: OccupancyGrid
    @type start: (int, int)
    @type end: (int, int)
    """
    if not is_visitable_point(map_grid, augmented_occ, end):
        return inf
    else:
        return euclidean_distance(start[0], start[1], end[0], end[1])


def cost_estimate(start, end):
    """Estimate the cost from start point to end point.

    Here we just simply use euclidean distance.
    @type start: (int, int)
    @type end: (int, int)
    """
    return euclidean_distance(start[0], start[1], end[0], end[1])


def reconstruct_path(came_from, current):
    """Reconstruct the WayPoint path from the initial(start) point to the current point.

    @type came_from: dict
    @type current: (int, int)
    """
    total_path = [current]
    while current in came_from:
        current = came_from[current]
        total_path.append(current)
    return total_path


def is_visitable_point(map_grid, augmented_occ, point, allow_unknown=True):
    """Check if a point is visitable.
    A point is not visitable if its occ(or augmented_occ) >= occ_threshold. When allow_unknown is False, it's not
    visitable either if its occ(or augmented_occ) == -1, otherwise -1 will be treated as normal occ value (means always
    <= threshold)
    """
    occ = map_grid.data[point[1] * map_grid.info.width + point[0]]
    if not allow_unknown and occ == -1:
        return False
    if occ >= occ_threshold:
        return False
    if point in augmented_occ:
        aug_occ = augmented_occ[point]
        if not allow_unknown and aug_occ == -1:
            return False
        if aug_occ >= occ_threshold:
            return False
    return True


def is_valid_point(map_grid, point):
    """Check if a point in the map is valid.
    A point is invalid if and only if the point is out of the range of the map.
    @type map_grid: OccupancyGrid
    @type point: (int, int)
    """
    x = point[0]
    y = point[1]
    width = map_grid.info.width
    height = map_grid.info.height
    return 0 <= x < width and 0 <= y < height


def neighbour_points(map_grid, point):
    """Get the valid neighbouring points of a given point based on the map info.
    @type map_grid: OccupancyGrid
    @type point: (int, int)
    """
    point_x = point[0]
    point_y = point[1]
    neighbours = [
        (point_x - 1, point_y - 1), (point_x, point_y - 1), (point_x + 1, point_y - 1),
        (point_x - 1, point_y), (point_x + 1, point_y),
        (point_x - 1, point_y + 1), (point_x, point_y + 1), (point_x + 1, point_y + 1)
    ]
    return [p for p in neighbours if is_valid_point(map_grid, p)]


def get_points_in_radius(center_x, center_y, radius, box_size, map_width, map_height):
    """Get points within the circular area around a center point with a given radius.
    The center point and points at border will be included.
    This function should be used as a generator to improve efficiency.
    box_size is to further limit the scanning area under a square area around the center point of this size.
    map_width/map_height is to ensure we only get valid points in the map.
    """
    min_i = max(center_y - box_size, 0)
    max_i = min(center_y + box_size, map_height - 1)
    min_j = max(center_x - box_size, 0)
    max_j = min(center_x + box_size, map_width - 1)
    radius_square = radius * radius
    for x in range(min_j, max_j + 1):
        for y in range(min_i, max_i + 1):
            # we may use function "euclidean_distance", but squared comparison seems more efficient
            # (save about 25% time)
            dx = x - center_x
            dy = y - center_y
            if dx * dx + dy * dy <= radius_square:
                yield (x, y)


def preprocess_map(map_grid):
    """Preprocess the map and generate the augmented occ values for some of the points
    @type map_grid: OccupancyGrid
    """
    h = map_grid.info.height
    w = map_grid.info.width
    res = map_grid.info.resolution
    radius, box_size = get_influence_area_size(map_grid)
    half_max_map_size_in_cells = int(math.ceil(max_map_size / res / 2))
    min_i = max(0, h / 2 - half_max_map_size_in_cells)
    max_i = min(h - 1, h / 2 + half_max_map_size_in_cells + 1)
    min_j = max(0, w / 2 - half_max_map_size_in_cells)
    max_j = min(w - 1, w / 2 + half_max_map_size_in_cells + 1)
    augmented_occ = {}
    for i in range(min_i, max_i + 1):
        for j in range(min_j, max_j + 1):
            occ = map_grid.data[i * w + j]
            # for each unsafe point, spread the circular influence area by robot radius
            if occ != -1 and occ >= occ_threshold:
                for p in get_points_in_radius(j, i, radius, box_size, w, h):
                    if p not in augmented_occ or augmented_occ[p] < occ:
                        augmented_occ[p] = occ
    return augmented_occ


def get_influence_area_size(map_grid):
    """Get the size of the influence area that helps preprocessing map and optimize paths
    Both the circular and rectangular boundary size will be returned
    """
    robot_map_radius = robot_radius / map_grid.info.resolution * (1 + safety_margin_percentage)
    robot_map_radius_int = int(math.ceil(robot_map_radius))
    min_central_distance = robot_map_radius + math.sqrt(2)  # pessimistic calculation (blocks treated as circles)
    return min_central_distance, robot_map_radius_int


def a_star_search(map_grid, augmented_occ, start, goal, goal_range=0, **kwargs):
    """Use A* algorithm to compute the shortest path from the start point to the goal point in the given map.
    Warning: for sake of efficiency, goal_range checks the rectangular area rather than circular one, and if it's not 0,
     the returned path is not an optimal one.
    @type map_grid: OccupancyGrid
    @type augmented_occ: {}
    @type start: (int,int)
    @type goal: (int,int)
    @type goal_range: int
    """
    closed_set = kwargs['closed_set'] if 'closed_set' in kwargs else set()
    open_set = kwargs['open_set'] if 'open_set' in kwargs else set()
    came_from = kwargs['came_from'] if 'came_from' in kwargs else dict()
    g_score = kwargs['g_score'] if 'g_score' in kwargs else dict()
    f_score = kwargs['f_score'] if 'f_score' in kwargs else dict()
    open_heap = []  # priority queue for fast retrieval of the open point with the smallest f_score

    g_score[start] = 0
    f_score[start] = cost_estimate(start, goal)
    open_set.add(start)
    heapq.heappush(open_heap, (f_score[start], start))

    # when open set is not empty
    while len(open_heap) > 0:
        # pop the point with the smallest f_score from the open set
        current_f_score, current_point = heapq.heappop(open_heap)
        open_set.remove(current_point)
        # if smallest f_score has been INF, it means no path exists
        if current_f_score >= inf:
            break
        # if already reach the goal
        if abs(goal[0] - current_point[0]) <= goal_range and abs(goal[1] - current_point[1]) <= goal_range:
            return reconstruct_path(came_from, current_point)
        # add current point into closed set (do not compute for it any more)
        closed_set.add(current_point)
        # get all valid neighbouring points of current point
        for neighbour_point in neighbour_points(map_grid, current_point):
            # if already closed
            if neighbour_point in closed_set:
                continue
            # compute map distance from this point to the current central point
            distance = map_distance(map_grid, augmented_occ, current_point, neighbour_point)
            # compute tentative g_score
            tentative_g_score = g_score.get(current_point, inf) + distance
            # if already open
            in_open_set = neighbour_point in open_set
            if not in_open_set or tentative_g_score < g_score.get(neighbour_point, inf):
                # update came_from and scores
                came_from[neighbour_point] = current_point
                g_score[neighbour_point] = tentative_g_score
                f_score[neighbour_point] = tentative_g_score + cost_estimate(neighbour_point, goal)
                if not in_open_set:
                    # push it into open set
                    heapq.heappush(open_heap, (f_score[neighbour_point], neighbour_point))
                    open_set.add(neighbour_point)
    return []


def get_crossed_points(start_point, end_point):
    """Get all points crossed over by the line that connects the start point and the end point.
    This function helps find all the influenced points when a robot go straight from the start point to the end point.
    For efficiency, this function will be called as a generator.
    @type start_point: (int, int)
    @type end_point: (int, int)
    """
    dy = end_point[1] - start_point[1]
    dx = end_point[0] - start_point[0]
    # for simplicity, we need that start point is at left side, end point is at right side. If they do not meet this
    # requirement, we simply swap them
    if dx < 0:
        dx = -dx
        dy = -dy
        start_point, end_point = end_point, start_point
    if dx == 0:  # special case, need to avoid 0-division error in the "else" logic
        step_y = 1 if dy >= 0 else -1
        for y in range(step_y, dy, step_y):  # we can safely ignore y=0 and y=dy here
            yield (start_point[0], start_point[1] + y)
    else:  # we must have dx > 0 here
        step = 1.0 * dy / dx
        for x in range(0, dx + 1, 1):
            if x == 0:  # first x
                start_y = 1 if step >= 0 else -1
            elif step >= 0:
                start_y = int(0.5 + step * (x - 0.5))
            else:
                start_y = int(-0.5 + step * (x - 0.5))
            if x == dx:  # last x
                end_y = dy - 1 if step >= 0 else dy + 1
            elif step >= 0:
                end_y = int(math.ceil(0.5 + step * (x + 0.5))) - 1
            else:
                end_y = int(math.floor(-0.5 + step * (x + 0.5))) + 1
            step_y = 1 if step >= 0 else -1
            for y in range(start_y, end_y + step_y, step_y):
                yield (start_point[0] + x, start_point[1] + y)


def optimize_path(map_grid, augmented_occ, path):
    """Optimize path list returned from A* (and search path service).
    It do the following optimizations:
        1. For each point in this path, check if the robot will collide with the unknown area when it goes there, mark
            the first collision point and the angle pointing to the unknown area, and then cut out the remaining points
        2. Remove all the redundant middle points in the path, which will make it easier for the robot to follow, make
            the total path length even shorter, and still keep the path safe
    """
    if len(path) <= 0:
        return []
    new_path = []
    last_start_point = None
    last_end_point = None
    is_start_point = True
    width = map_grid.info.width
    height = map_grid.info.height
    radius, box_size = get_influence_area_size(map_grid)
    point_index = len(path) - 1
    while point_index >= 0:
        point = path[point_index]
        if is_start_point:
            is_start_point = False
            last_start_point = point
            new_path.append(point)  # starting point is always returned
        else:
            # A* treats the unknown area as normal empty space to estimate the currently most likely best path (rather
            # than returns no path), but when we move the robot, we need to let it stand still or watch around when it
            # is about to enter or pass by an unknown area until the map is updated and new path is computed.
            collide_with_unknown_area = False
            unknown_point = None
            for p in get_points_in_radius(point[0], point[1], radius, box_size, width, height):
                if map_grid.data[p[1] * width + p[0]] == -1:
                    unknown_point = p
                    collide_with_unknown_area = True
                    break
            if collide_with_unknown_area:
                last_safe_point = last_start_point
                if last_end_point is not None:
                    new_path.append(last_end_point)
                    last_safe_point = last_end_point
                    last_end_point = None  # reset last end point to avoid appending it again
                angle = math.atan2(unknown_point[1] - last_safe_point[1], unknown_point[0] - last_safe_point[0])
                # record the angle to the unknown area and ignore following points
                new_path.append((path_unknown_marker, angle))
                break
            # get all points crossed over by the line that connects the last start point and the last end point, if
            # there are no obstacles or unknown areas in these points, we may ignore all the middle points between the
            # last start point and last end point in the A* path list
            is_safe_line = True
            for cross_point in get_crossed_points(last_start_point, point):
                if not is_valid_point(map_grid, cross_point):  # should never happen, but check for safety
                    rospy.logwarn(
                        'Tried to access invalid point: (%s) when computing crossed points' % str(cross_point))
                    is_safe_line = False
                    break
                if not is_visitable_point(map_grid, augmented_occ, cross_point, False):
                    is_safe_line = False
                    break
            if is_safe_line:
                last_end_point = point
            else:
                if last_end_point is None:  # just check for safety, this point should always exists when code goes here
                    rospy.logwarn('Path returned from A* is broken at %s' % str(last_start_point))
                    return []
                last_start_point = last_end_point
                new_path.append(last_end_point)
                last_end_point = None
                point_index += 1  # handle current point again in the next iteration since we have a new start point
        point_index -= 1
    if last_end_point is not None:  # last point need to be appended
        new_path.append(last_end_point)
    new_path.reverse()
    return new_path


def convert_way_point_to_map_cell(map_grid, map_point):
    """
    @type map_grid: OccupancyGrid
    @type map_point: WayPoint
    """
    origin = map_grid.info.origin.position
    resolution = map_grid.info.resolution
    return map_point.x - int(origin.x / resolution), map_point.y - int(origin.y / resolution)


def convert_point_to_map_cell(map_grid, map_point):
    """
    @type map_grid: OccupancyGrid
    @type map_point: (int, int)
    """
    origin = map_grid.info.origin.position
    resolution = map_grid.info.resolution
    return map_point[0] - int(origin.x / resolution), map_point[1] - int(origin.y / resolution)


def convert_map_cell_to_point(map_grid, cell):
    """
    @type map_grid: OccupancyGrid
    @type cell: (int, int)
    """
    origin = map_grid.info.origin.position
    resolution = map_grid.info.resolution
    return cell[0] + int(origin.x / resolution), cell[1] + int(origin.y / resolution)


def convert_map_cells_to_way_points(map_grid, cells):
    """
    @type map_grid: OccupancyGrid
    @type cells: [(int, int)]
    """
    origin = map_grid.info.origin.position
    resolution = map_grid.info.resolution
    return [WayPoint(cell[0] + int(origin.x / resolution), cell[1] + int(origin.y / resolution)) for cell in cells]


def handle_search_path(request):
    """Handler function for a single service call.
    @type request: SearchPathRequest
    """
    map_info = request.map.info
    rospy.loginfo('Searching shortest path from (%d, %d) to (%d, %d) on a %d * %d map...' %
                  (request.start.x, request.start.y, request.goal.x, request.goal.y,
                   map_info.width, map_info.height))
    # convert back to normal sequence type
    start_seq = convert_way_point_to_map_cell(map_info, request.start)
    goal_seq = convert_way_point_to_map_cell(map_info, request.goal)
    augmented_occ = preprocess_map(request.map)
    path = a_star_search(request.map, augmented_occ, start_seq, goal_seq, request.goal_distance)
    path = optimize_path(request.map, augmented_occ, path)
    if len(path) <= 0:
        rospy.logwarn('Search path failed')
    else:
        rospy.loginfo('Found shortest path, length=%d' % len(path))
    path_msg = convert_map_cells_to_way_points(map_info, path)
    return SearchPathResponse(path_msg)


def search_path_server():
    """Initialize "search_path_server" node and register "search_path" service."""
    rospy.init_node('search_path_server')
    rospy.Service('search_path', SearchPath, handle_search_path)
    rospy.loginfo('Search path server is ready.')
    rospy.spin()


if __name__ == '__main__':
    search_path_server()
