"""
蓝图自动确认工具 v4
====================
完整流程：
1. 扫描背包非空格子（列优先：1-4-7-10, 2-5-8-11, 3-6-9-12...）
2. Ctrl+左键点击格子 → 放入蓝图
3. 识别蓝图小格子，依次点击
4. 每点小格子 → 出现人物 → 点第一个人物
5. 所有小格子点完 → 点确认
6. Ctrl+左键点蓝图槽 → 背包回来
7. 下一个蓝图，循环直到完成

操作: F2=开始 F3=停止 ESC=停止
"""

import sys
import ctypes
import time
import os
import threading
import json
from ctypes import wintypes

import numpy as np
import cv2
import pyautogui
import mss
import win32clipboard

import tkinter as tk
from tkinter import ttk

# ============================================================
# Win32
# ============================================================
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = True

# ============================================================
# 背包坐标（800x600 基准，来自 backpack_coords_800x600.json）
# ============================================================
# 5行×12列 = 60格
# 左上角锚点(454,337) → yFromBottom=263
# 右下角锚点(777,459) → yFromBottom=141

BASE_CLIENT_W = 800
BASE_CLIENT_H = 600

# 从JSON提取的坐标（800x600基准）
BP_TOP_LEFT_X = 454
BP_TOP_LEFT_Y = 337  # 从顶部算起
BP_BOTTOM_RIGHT_X = 777
BP_BOTTOM_RIGHT_Y = 459

# 计算格子间距
NUM_ROWS = 5
NUM_COLS = 12
COL_WIDTH = (BP_BOTTOM_RIGHT_X - BP_TOP_LEFT_X) / (NUM_COLS - 1)  # ≈29.36
ROW_HEIGHT = (BP_BOTTOM_RIGHT_Y - BP_TOP_LEFT_Y) / (NUM_ROWS - 1)  # ≈30.5

# 第一行距底部距离（600-337=263）
FIRST_ROW_FROM_BOTTOM = BASE_CLIENT_H - BP_TOP_LEFT_Y  # 263


def project_backpack_slots(client_w, client_h):
    """
    右下锚定投影（与参考脚本一致）：
    proj_x = client_w - (BASE_CLIENT_W - ref_x) * scale
    proj_y = client_h - (BASE_CLIENT_H - ref_y) * scale

    返回客户区坐标，调用时需加上 client_left, client_top 得到屏幕坐标
    """
    scale = client_h / BASE_CLIENT_H

    slots = []
    for col in range(NUM_COLS):
        for row in range(NUM_ROWS):
            # 相对于客户区左上角的基准坐标
            ref_x = BP_TOP_LEFT_X + col * COL_WIDTH
            ref_y = BP_TOP_LEFT_Y + row * ROW_HEIGHT

            # 投影：右下锚定 + 高度缩放
            x = int(client_w - (BASE_CLIENT_W - ref_x) * scale)
            y = int(client_h - (BASE_CLIENT_H - ref_y) * scale)

            slots.append((col, row, x, y))

    return slots


def get_slots_col_major(slots):
    """返回列优先排序的格子 (col0全部行, col1全部行, ...)"""
    # slots 已经是 col→row 顺序，直接返回
    return slots


# ============================================================
# 配置
# ============================================================
LOG_FILE = "logs/run_blueprint.log"

# 人物大框
AGENT_MODAL_Y_RATIO = 0.39
AGENT_MODAL_H_RATIO = 0.2333
AGENT_MODAL_W_BY_HEIGHT = 0.5

# 人物卡片
LEVEL_COLOR_BGR = np.array([29, 210, 246], dtype=np.float32)
LEVEL_COLOR_TOLERANCE = 40
CARD_OFFSET_RATIO = 0.04

# 蓝图小格子检测 - detect_cells_test.py 验证过的算法
CELL_COLOR_BGR = np.array([0x8e, 0xad, 0xc3], dtype=np.float32)
COLOR_DISTANCE_MAX = 30  # detect_cells_test.py 的值

# 确认按钮 - 颜色 #241001 附近
CONFIRM_BUTTON_BGR = np.array([0x01, 0x10, 0x24], dtype=np.float32)

# 蓝图槽 - 颜色 #05061f 附近
BLUEPRINT_SLOT_BGR = np.array([0x1f, 0x06, 0x05], dtype=np.float32)
BLUEPRINT_SLOT_Y_RATIO = 0.85

# 颜色容差
COLOR_TOLERANCE = 50

# 空格子颜色（#060606 附近）
EMPTY_SLOT_COLOR_BGR = np.array([6, 6, 6], dtype=np.float32)
EMPTY_SLOT_TOLERANCE = 30
EMPTY_SLOT_SAMPLE_RADIUS = 8


# ============================================================
# 颜色搜索
# ============================================================
def find_color_in_region(img, target_bgr, region_x1_ratio, region_y1_ratio,
                         region_x2_ratio, region_y2_ratio, tolerance=COLOR_TOLERANCE):
    """
    在指定区域搜索颜色最接近的点
    返回 (screen_x, screen_y) 或 None
    """
    h, w = img.shape[:2]
    x1 = int(w * region_x1_ratio)
    y1 = int(h * region_y1_ratio)
    x2 = int(w * region_x2_ratio)
    y2 = int(h * region_y2_ratio)

    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    # 计算颜色距离
    diff = np.sqrt(np.sum((roi.astype(np.float32) - target_bgr) ** 2, axis=2))

    # 找到最小距离
    min_val = diff.min()
    if min_val > tolerance:
        return None

    # 找到所有符合条件的点
    mask = diff <= tolerance
    points = np.where(mask)

    if len(points[0]) == 0:
        return None

    # 取中心点
    center_y = int(np.mean(points[0])) + y1
    center_x = int(np.mean(points[1])) + x1

    return (center_x, center_y)


# ============================================================
# 日志
# ============================================================
def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


# ============================================================
# Win32 辅助
# ============================================================
def get_client_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top)


def get_window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def find_poe_window():
    result = []

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        if "流放之路" in buf.value:
            result.append(hwnd)
            return False
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
    return result[0] if result else None


def capture(hwnd):
    rect = get_client_rect(hwnd)
    if not rect:
        return None, None
    left, top, w, h = rect
    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": w, "height": h}
        img = np.array(sct.grab(monitor))[:, :, :3]
        return img, rect


def click(x, y, delay=0.1):
    pyautogui.click(x, y)
    time.sleep(delay)


def ctrl_click(x, y, delay=0.2):
    pyautogui.moveTo(x, y)
    time.sleep(0.05)
    pyautogui.keyDown('ctrl')
    time.sleep(0.05)
    pyautogui.click(x, y)
    time.sleep(0.05)
    pyautogui.keyUp('ctrl')
    time.sleep(delay)


# ============================================================
# 检测函数
# ============================================================
def is_slot_empty(img, cx, cy):
    """检测格子中心是否为空（颜色接近 #060606）"""
    h, w = img.shape[:2]
    r = EMPTY_SLOT_SAMPLE_RADIUS
    y1, y2 = max(0, cy - r), min(h, cy + r + 1)
    x1, x2 = max(0, cx - r), min(w, cx + r + 1)

    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return True

    mean_color = np.mean(region, axis=(0, 1))
    dist = np.sqrt(np.sum((mean_color - EMPTY_SLOT_COLOR_BGR) ** 2))
    return dist < EMPTY_SLOT_TOLERANCE


def scan_nonempty_slots(img, slots):
    """扫描非空格子（有蓝图的），返回 [(index, x, y), ...]"""
    found = []
    for idx, (col, row, x, y) in enumerate(slots):
        empty = is_slot_empty(img, x, y)
        if not empty:
            found.append((idx, x, y))
            log(f"  格子[{col+1},{row+1}] ({x},{y}) 非空")
    return found


def find_agent_card(img, client_w, client_h):
    """找第一个人物卡片"""
    box_w = int(AGENT_MODAL_W_BY_HEIGHT * client_h)
    box_h = int(AGENT_MODAL_H_RATIO * client_h)
    box_x = client_w // 2 - box_w // 2
    box_y = int(AGENT_MODAL_Y_RATIO * client_h)

    region = img[box_y:box_y+box_h, box_x:box_x+box_w]
    if region.size == 0:
        return None

    diff = region.astype(np.float32) - LEVEL_COLOR_BGR
    distance = np.sqrt(np.sum(diff ** 2, axis=2))
    mask = (distance < LEVEL_COLOR_TOLERANCE).astype(np.uint8) * 255

    col_sum = np.sum(mask > 0, axis=0)
    active_cols = np.where(col_sum > 3)[0]

    if len(active_cols) == 0:
        return None

    # 最左边第一个连续区域
    first_start = active_cols[0]
    first_end = first_start
    for col in active_cols[1:]:
        if col - first_end <= 5:
            first_end = col
        else:
            break

    center_x_rel = (first_start + first_end) // 2
    col_mask = mask[:, first_start:first_end+1]
    rows_with_color = np.where(np.any(col_mask > 0, axis=1))[0]
    if len(rows_with_color) == 0:
        return None

    card_x = box_x + center_x_rel
    card_y = box_y + rows_with_color[0] - int(CARD_OFFSET_RATIO * client_h)
    return (card_x, card_y)


def _detect_cell_groups(binary_img):
    """detect_cells_test.py: 检测格子组（三连组）"""
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 15))
    closed = cv2.morphologyEx(binary_img, cv2.MORPH_CLOSE, kernel_h, iterations=2)
    closed = cv2.morphologyEx(closed, cv2.MORPH_CLOSE, kernel_v, iterations=2)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)

    groups = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        bw = stats[i, cv2.CC_STAT_WIDTH]
        bh = stats[i, cv2.CC_STAT_HEIGHT]
        cx, cy = centroids[i]

        aspect = bw / bh if bh > 0 else 0
        if (30 <= bw <= 800 and 10 <= bh <= 400 and
            1.0 <= aspect <= 6.0 and area >= 800):
            groups.append({'x': x, 'y': y, 'w': bw, 'h': bh, 'area': area, 'cx': cx, 'cy': cy})

    groups.sort(key=lambda g: (g['y'], g['x']))
    return groups, closed


def _split_group(binary_img, group, num_cells=3):
    """detect_cells_test.py: 分割组为单个格子"""
    gx, gy, gw, gh = group['x'], group['y'], group['w'], group['h']
    region = binary_img[gy:gy + gh, gx:gx + gw]

    col_proj = np.sum(region > 0, axis=0)
    threshold = col_proj.max() * 0.15
    low_cols = np.where(col_proj < threshold)[0]

    if len(low_cols) == 0:
        return None

    gaps = []
    start = low_cols[0]
    for i in range(1, len(low_cols)):
        if low_cols[i] - low_cols[i - 1] > 2:
            gap_width = low_cols[i - 1] - start + 1
            if gap_width >= 3:
                gaps.append((start, low_cols[i - 1]))
            start = low_cols[i]
    gap_width = low_cols[-1] - start + 1
    if gap_width >= 3:
        gaps.append((start, low_cols[-1]))

    if len(gaps) < num_cells - 1:
        return None

    gap_centers = [(g[0] + g[1]) / 2 for g in gaps]
    expected_positions = [gw * (i + 1) / num_cells for i in range(num_cells - 1)]

    matched = 0
    used = set()
    for exp_pos in expected_positions:
        best_idx = -1
        best_dist = float('inf')
        for gi, gc in enumerate(gap_centers):
            if gi not in used:
                dist = abs(gc - exp_pos)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = gi
        if best_idx >= 0 and best_dist < gw * 0.25:
            matched += 1
            used.add(best_idx)

    if matched < num_cells - 1:
        return None

    cell_width = gw / num_cells
    cells = []
    for i in range(num_cells):
        cells.append({'x': int(gx + i * cell_width), 'y': gy, 'w': int(cell_width), 'h': gh})
    return cells


def detect_blueprint_cells(img, client_w, client_h):
    """蓝图小格子检测 - 与 detect_cells_test.py 完全一致的全屏检测"""
    # 全屏颜色检测（与 detect_cells_test.py 一致）
    diff = img.astype(np.float32) - CELL_COLOR_BGR
    distance = np.sqrt(np.sum(diff ** 2, axis=2))
    binary = (distance < COLOR_DISTANCE_MAX).astype(np.uint8) * 255

    # 屏蔽干扰（相对全屏，与 detect_cells_test.py 一致）
    h, w = img.shape[:2]
    # 上方8%
    y1, y2 = 0, int(0.08 * h)
    x1, x2 = 0, w
    binary[y1:y2, x1:x2] = 0
    # 左侧15%
    y1, y2 = 0, h
    x1, x2 = 0, int(0.15 * w)
    binary[y1:y2, x1:x2] = 0

    # 检测格子组
    groups, _ = _detect_cell_groups(binary)

    # 分割为单个格子
    all_cells = []
    for group in groups:
        cells = _split_group(binary, group)
        if cells:
            for c in cells:
                all_cells.append((c['x'] + c['w'] // 2, c['y'] + c['h'] // 2))

    return all_cells


# ============================================================
# 主流程
# ============================================================
class BlueprintAuto:
    def __init__(self, log_callback=None, debug=False):
        self.running = False
        self.poe_hwnd = None
        self.log_callback = log_callback
        self.debug = debug

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            log(msg)

    def start(self):
        self.running = True
        self.log("=" * 60)
        self.log("开始运行")

        self.poe_hwnd = find_poe_window()
        if not self.poe_hwnd:
            self.log("错误: 未找到游戏窗口")
            return

        img, rect = capture(self.poe_hwnd)
        if img is None:
            self.log("错误: 截图失败")
            return

        client_w, client_h = rect[2], rect[3]
        self.log(f"客户区: {client_w}x{client_h}")

        # 投影背包格子
        slots = project_backpack_slots(client_w, client_h)
        self.log(f"背包格子: {len(slots)} 个")

        # 扫描非空格子
        nonempty = scan_nonempty_slots(img, slots)
        self.log(f"非空格子: {len(nonempty)} 个")

        if not nonempty:
            self.log("背包无蓝图，退出")
            return

        # 蓝图槽位置
        bp_slot_x = client_w // 2
        bp_slot_y = int(BLUEPRINT_SLOT_Y_RATIO * client_h)

        # 依次处理每个蓝图
        for idx, (slot_idx, slot_x, slot_y) in enumerate(nonempty):
            if not self.running:
                break

            self.log(f"\n{'='*40}")
            self.log(f"处理第 {idx+1}/{len(nonempty)} 个蓝图")

            # 1. 检查格子是否为蓝图（Ctrl+C 复制，检查剪切板）
            self.log(f"[1] 检查格子 ({slot_x},{slot_y})")
            pyautogui.moveTo(slot_x, slot_y)
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.2)

            # 读取剪切板
            try:
                win32clipboard.OpenClipboard()
                clipboard_text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
            except:
                clipboard_text = ""

            if "蓝图" not in clipboard_text:
                self.log(f"  跳过: 不是蓝图")
                continue

            self.log(f"  确认为蓝图，Ctrl+左键放入")
            ctrl_click(slot_x, slot_y)
            time.sleep(0.3)

            # 2. 截图并检测蓝图小格子（重试3次）
            cells = []
            img2 = None
            for attempt in range(3):
                time.sleep(0.3)
                img2, rect2 = capture(self.poe_hwnd)
                if img2 is None:
                    continue
                cells = detect_blueprint_cells(img2, rect2[2], rect2[3])
                if len(cells) > 0:
                    break
                self.log(f"  未检测到小格子，重试 {attempt+1}/3...")

            # 保存截图用于调试（仅debug模式）
            if self.debug and img2 is not None:
                os.makedirs("debug", exist_ok=True)
                cv2.imwrite(f"debug/blueprint_slot_{idx}.png", img2)
            self.log(f"[2] 检测到 {len(cells)} 个小格子")

            if len(cells) == 0:
                self.log("  错误: 3次重试后仍未检测到小格子，停止")
                self.running = False
                return

            for ci, (cx, cy) in enumerate(cells):
                if not self.running:
                    break

                self.log(f"  小格子#{ci+1} ({cx},{cy})")
                # 点击小格子
                pyautogui.moveTo(cx, cy)
                time.sleep(0.1)
                pyautogui.click()
                time.sleep(0.4)  # 等弹窗出现

                # 检测人物卡片（重试3次）
                card = None
                for attempt in range(3):
                    img2, rect2 = capture(self.poe_hwnd)
                    if img2 is None:
                        continue
                    if self.debug and ci == 0 and attempt == 0:
                        os.makedirs("debug", exist_ok=True)
                        cv2.imwrite(f"debug/agent_modal_{idx}.png", img2)
                    card = find_agent_card(img2, rect2[2], rect2[3])
                    if card:
                        break
                    self.log(f"    未检测到人物卡片，重试 {attempt+1}/3...")
                    time.sleep(0.3)

                if card:
                    self.log(f"  ✓ 人物卡片 ({card[0]},{card[1]})")
                    # 点击人物卡片
                    pyautogui.moveTo(card[0], card[1])
                    time.sleep(0.1)
                    pyautogui.click()
                    time.sleep(0.4)  # 等弹窗关闭
                else:
                    self.log("  错误: 3次重试后仍未检测到人物卡片，停止")
                    self.running = False
                    return

            # 4. 点击确认按钮（颜色识别，在默认坐标附近搜索）
            default_confirm_x = client_w // 2
            default_confirm_y = int(0.94 * client_h)
            img2, rect2 = capture(self.poe_hwnd)
            if img2 is not None:
                # 在默认坐标附近±100像素范围搜索
                confirm_pos = find_color_in_region(
                    img2, CONFIRM_BUTTON_BGR,
                    max(0, (default_confirm_x - 100) / client_w),
                    max(0, (default_confirm_y - 100) / client_h),
                    min(1, (default_confirm_x + 100) / client_w),
                    min(1, (default_confirm_y + 100) / client_h)
                )
                if confirm_pos:
                    confirm_x, confirm_y = confirm_pos
                    self.log(f"[4] 确认按钮 ({confirm_x},{confirm_y}) [颜色识别]")
                    # 多次点击确保生效
                    click(confirm_x, confirm_y)
                    time.sleep(0.2)
                    click(confirm_x, confirm_y)
                else:
                    self.log("[4] 未检测到确认按钮颜色，使用默认坐标")
                    click(default_confirm_x, default_confirm_y)
                    time.sleep(0.2)
                    click(default_confirm_x, default_confirm_y)
            else:
                self.log("[4] 截图失败，使用默认坐标")
                click(default_confirm_x, default_confirm_y)
                time.sleep(0.2)
                click(default_confirm_x, default_confirm_y)
            time.sleep(0.3)

            # 5. Ctrl+左键蓝图槽 → 放回背包（颜色识别，在默认坐标附近搜索）
            img2, rect2 = capture(self.poe_hwnd)
            if img2 is not None:
                # 在默认坐标附近±100像素范围搜索
                slot_pos = find_color_in_region(
                    img2, BLUEPRINT_SLOT_BGR,
                    max(0, (bp_slot_x - 100) / client_w),
                    max(0, (bp_slot_y - 100) / client_h),
                    min(1, (bp_slot_x + 100) / client_w),
                    min(1, (bp_slot_y + 100) / client_h)
                )
                if slot_pos:
                    slot_x, slot_y = slot_pos
                    self.log(f"[5] Ctrl+左键 蓝图槽 ({slot_x},{slot_y}) [颜色识别]")
                    ctrl_click(slot_x, slot_y)
                else:
                    self.log("[5] 未检测到蓝图槽颜色，使用默认坐标")
                    ctrl_click(bp_slot_x, bp_slot_y)
            else:
                self.log("[5] 截图失败，使用默认坐标")
                ctrl_click(bp_slot_x, bp_slot_y)
            time.sleep(0.5)  # 等背包界面完全恢复

            self.log(f"第 {idx+1} 个蓝图完成 ✓")

            # 蓝图之间间隔（确保背包界面就绪）
            if idx < len(nonempty) - 1:
                time.sleep(0.3)

        self.log(f"\n全部完成！共处理 {len(nonempty)} 个蓝图")

    def stop(self):
        self.running = False
        log("用户停止")


# ============================================================
# GUI
# ============================================================
class App:
    def __init__(self, debug=False):
        self.root = tk.Tk()
        self.root.title("蓝图自动确认工具 v4")
        self.root.geometry("450x320")
        self.root.resizable(False, False)

        self.auto = BlueprintAuto(log_callback=self.log_msg, debug=debug)

        ttk.Label(self.root, text="F2: 开始 | F3/ESC: 停止", font=('Arial', 12)).pack(pady=10)
        ttk.Label(self.root, text="请先将蓝图视角拉到最大！", font=('Arial', 10), foreground='red').pack()
        self.lbl_status = ttk.Label(self.root, text="状态: 空闲", foreground='gray')
        self.lbl_status.pack()

        frame = ttk.LabelFrame(self.root, text="日志 (logs/run_blueprint.log)", padding=5)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.txt = tk.Text(frame, height=10, state=tk.DISABLED, font=('Consolas', 9))
        self.txt.pack(fill=tk.BOTH, expand=True)

        self.last_f2 = False
        self.last_f3 = False
        self.poll_hotkeys()

    def log_msg(self, msg):
        self.txt.config(state=tk.NORMAL)
        self.txt.insert(tk.END, msg + "\n")
        self.txt.see(tk.END)
        self.txt.config(state=tk.DISABLED)

    def poll_hotkeys(self):
        f2 = user32.GetAsyncKeyState(0x71) & 0x8000
        f3 = user32.GetAsyncKeyState(0x72) & 0x8000
        esc = user32.GetAsyncKeyState(0x1B) & 0x8000

        if f2 and not self.last_f2:
            self.start()
        if (f3 or esc) and not self.last_f3:
            self.stop()

        self.last_f2 = bool(f2)
        self.last_f3 = bool(f3 or esc)
        self.root.after(50, self.poll_hotkeys)

    def start(self):
        if self.auto.running:
            return
        self.lbl_status.config(text="状态: 运行中", foreground='green')
        threading.Thread(target=self.run_auto, daemon=True).start()

    def stop(self):
        self.auto.stop()
        self.lbl_status.config(text="状态: 已停止", foreground='red')

    def run_auto(self):
        self.auto.start()
        self.root.after(0, lambda: self.lbl_status.config(text="状态: 完成", foreground='gray'))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="蓝图自动确认工具 v4")
    parser.add_argument("--debug", action="store_true", help="启用调试模式（保存截图）")
    args = parser.parse_args()

    app = App(debug=args.debug)
    app.root.mainloop()


if __name__ == "__main__":
    main()
