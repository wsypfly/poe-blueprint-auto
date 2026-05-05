# 蓝图自动确认工具

流放之路蓝图自动确认工具，支持任意分辨率。

## 功能

- 自动扫描背包中的蓝图
- 自动点击蓝图小格子
- 自动选择人物卡片
- 自动确认并取回蓝图

## 使用方法

### 方式一：直接运行 exe

1. 下载 `蓝图自动确认工具.exe`
2. 以管理员身份运行
3. 按 F2 开始，F3/ESC 停止

### 方式二：Python 源码运行

```bash
pip install -r requirements.txt
python run_blueprint_auto.py
```

## 操作步骤

1. 打开游戏，进入计划桌界面
2. 将蓝图放入背包（按列优先顺序）
3. 在蓝图界面，将视角拉到最大（滚轮拉满）
4. 启动工具，按 F2 开始

## 操作说明

| 按键 | 功能 |
|------|------|
| F2 | 开始运行 |
| F3 / ESC | 停止运行 |

## 注意事项

- 游戏窗口必须在前台
- 蓝图视角必须拉到最大
- 需要管理员权限运行
- 支持任意分辨率（蓝图界面需完整显示）

## 调试模式

```bash
python run_blueprint_auto.py --debug
```

启用后截图保存到 `debug/` 目录，便于排查问题。

## 依赖

- Python 3.8+
- pyautogui
- opencv-python
- numpy
- mss
- pywin32
- keyboard
