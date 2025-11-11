from datetime import datetime, timezone, timedelta
from importlib import resources
import json
import os
import pathlib
import subprocess
import sqlite3

import urllib.request
import threading

import bottle

from . import models
from . import settings

app = bottle.Bottle()

bottle.TEMPLATE_PATH += [
    resources.files('x6100_webserver').joinpath('views'),
]

STATIC_PATH = resources.files('x6100_webserver').joinpath('static')


# Bands API

@app.get('/api/bands')
def get_bands(dbcon):
    bands = models.read_bands(dbcon)
    bottle.response.content_type = 'application/json'
    return json.dumps([x.asdict() for x in bands])


@app.put('/api/bands')
def add_band(dbcon):
    data = bottle.request.json
    try:
        band_param = models.BandParams(**data)
        models.add_band(dbcon, band_param)
        bottle.response.status = 201
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}


@app.post('/api/bands/<band_id:int>')
def update_band(band_id, dbcon):
    data = bottle.request.json
    try:
        band_param = models.BandParams(id=band_id, **data)
        models.update_band(dbcon, band_param)
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}


@app.delete('/api/bands/<band_id:int>')
def delete_band(band_id, dbcon):
    try:
        models.delete_band(dbcon, band_id)
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}


# Digital modes routes

@app.get('/api/digital_modes')
def get_digital_modes(dbcon):
    d_modes = models.read_digital_modes(dbcon)
    bottle.response.content_type = 'application/json'
    return json.dumps([x.asdict() for x in d_modes])


@app.put('/api/digital_modes')
def add_digital_mode(dbcon):
    data = bottle.request.json
    try:
        d_mode = models.DigitalMode(**data)
        models.add_digital_mode(dbcon, d_mode)
        bottle.response.status = 201
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}


@app.post('/api/digital_modes/<mode_id:int>')
def update_digital_mode(mode_id, dbcon):
    data = bottle.request.json
    try:
        d_mode = models.DigitalMode(id=mode_id, **data)
        models.update_digital_mode(dbcon, d_mode)
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}


@app.delete('/api/digital_modes/<mode_id:int>')
def delete_digital_mode(mode_id, dbcon):
    try:
        models.delete_digital_mode(dbcon, mode_id)
        return {"status": "OK"}
    except ValueError as e:
        bottle.response.status = 400
        return {"status": "error", "msg": str(e)}

# Main routes

@app.route('/static/<filepath:path>')
def server_static(filepath):
    return bottle.static_file(filepath, root=STATIC_PATH)


@app.route('/')
def home():
    return bottle.template('index')


@app.route('/bands')
def bands():
    return bottle.template('bands')


@app.route('/digital_modes')
def digital_modes():
    return bottle.template('digital_modes')

@app.route('/files/')
@app.route('/files/<filepath:path>')
@app.route('/files/<filepath:path>/')
def files(filepath=""):
    path = pathlib.Path(settings.FILEBROWSER_PATH) / filepath
    if path.is_file():
        os.sync()
        response = bottle.static_file(str(path.relative_to(settings.FILEBROWSER_PATH)), root=settings.FILEBROWSER_PATH, download=True)
        response.set_header("Cache-Control", "private, no-cache, no-store")
        return response
    else:
        dirs = []
        files = []
        for item in sorted(path.iterdir()):
            if item.is_dir():
                dirs.append(item.relative_to(path))
            else:
                files.append(item.relative_to(path))
        return bottle.template('files', dirs=dirs, files=files)

# Timezone routes


@app.route('/time')
def time_editor():
    return bottle.template('time')


@app.get('/api/get_time')
def get_time():
    tz = timezone(timedelta())
    server_time = datetime.now(tz).isoformat()
    bottle.response.content_type = 'application/json'
    return {"server_time": server_time}


def update_time_by_ntp(server_address):
    ntp_args = ["ntpdate", "-u", server_address]
    p = subprocess.Popen(
        ntp_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _, errs = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        _, errs = p.communicate()
        bottle.response.status = 500
        return {"status": "error", "msg": "NTP update timeout"}

    if p.returncode != 0:
        bottle.response.status = 500
        return {"status": "error", "msg": f"NTP update failed: {errs.decode()}"}

    return {"status": "success", "msg": "NTP update successful"}


@app.post('/api/update_time')
def update_time():
    data = bottle.request.json

    update_mode = data.get("update_mode")
    if not update_mode:
        bottle.response.status = 400
        return {"status": "error", "msg": "update_mode is required"}

    if update_mode == "ntp":
        server_address = data.get("server_address")
        return update_time_by_ntp(server_address)

    elif update_mode == "manual":
        manual_time = data.get("manual_time")
        if not manual_time:
            bottle.response.status = 400
            return {"status": "error", "msg": "manual_time is required"}

        try:
            # Update system time manually
            manual_time = datetime.strptime(manual_time, "%Y-%m-%d %H:%M:%S")
            subprocess.run(
                ["date", "-s", manual_time.strftime("%Y-%m-%d %H:%M:%S")], check=True)
            return {"status": "success", "msg": "Server time updated manually"}
        except Exception as e:
            bottle.response.status = 500
            return {"status": "error", "msg": f"Failed to set manual time: {str(e)}"}

    else:
        bottle.response.status = 400
        return {"status": "error", "msg": f"unknown update_mode: {update_mode}"}


@app.get('/api/get_timezone')
def get_timezone():
    """Get the current server timezone."""
    try:
        p = subprocess.run(["realpath", "/etc/localtime"],
                           stdout=subprocess.PIPE, check=True)
        timezone_path = p.stdout.decode().strip()
        tz_list = timezone_path.split("/posix/")
        if len(tz_list) < 2:
            tz_list = timezone_path.split("/zoneinfo/")
        tz = tz_list[-1]
        return {"timezone": tz}
    except Exception as e:
        bottle.response.status = 500
        return {"status": "error", "msg": f"Failed to fetch timezone: {str(e)}"}


@app.post('/api/set_timezone')
def set_timezone():
    """Set the server timezone."""
    data = bottle.request.json
    timezone = data.get("timezone")
    if not timezone:
        bottle.response.status = 400
        return {"status": "error", "msg": "Timezone is required"}

    target_tz = f"/usr/share/zoneinfo/{timezone}"
    if not os.path.exists(target_tz):
        bottle.response.status = 400
        return {"status": "error", "msg": f"Invalid timezone: {timezone}"}

    try:
        subprocess.run(["ln", "-sf", target_tz, "/etc/localtime"], check=True)
        return {"status": "success", "msg": "Timezone updated successfully"}
    except subprocess.CalledProcessError as e:
        bottle.response.status = 500
        return {"status": "error", "msg": f"Failed to set timezone: {str(e)}"}


# Wavelog Sync routes

import bottle
import sqlite3
import threading
import urllib.request, urllib.error
import json
from datetime import datetime
import settings
import os

X6100_SYNC_DELAY = 0
X6100_SYNC_TIMER = None

ADI_LOG_PATH = "/mnt/ft_log.adi"


# ---------- 定时任务 ----------
def sync_poll_task():
    """周期性任务：读取 ADIF 日志并上传到 Wavelog"""
    with sqlite3.connect(settings.DB_PATH, check_same_thread=False) as conn:
        do_sync(conn)


# ---------- 页面 ----------
@app.route('/sync')
def sync():
    return bottle.template('sync')


# ---------- 上传逻辑 ----------
@app.post('/api/do_sync')
def do_sync(dbcon):
    """上传通联日志至 Wavelog（仅上传新增部分）"""
    try:
        data = bottle.request.json or {}
    except:
        data = {}

    # 从数据库读取配置
    if not data.get("key"):
        cur = dbcon.cursor()
        mapping = {
            "key": "sync_key",
            "endpoint": "sync_endpoint",
            "delay": "sync_delay",
            "station_profile_id": "sync_station_profile_id"
        }
        for field, name in mapping.items():
            row = cur.execute("SELECT val FROM params WHERE name = ?", (name,)).fetchone()
            if row:
                data[field] = row[0]

    if int(data.get('delay', 0)) <= 0 and not data.get('nodelay'):
        return "Auto sync disabled (delay=0)."

    # 日志文件检查
    if not os.path.exists(ADI_LOG_PATH):
        return f"Log file not found: {ADI_LOG_PATH}"

    # 获取上次同步偏移量
    cur = dbcon.cursor()
    row = cur.execute("SELECT val FROM params WHERE name = ?", ("sync_log_offset",)).fetchone()
    last_offset = int(row[0]) if row else 0

    # 打开文件并读取新内容
    filesize = os.path.getsize(ADI_LOG_PATH)
    if filesize <= last_offset:
        return "No new QSO records to upload."

    with open(ADI_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(last_offset)
        new_data = f.read().strip()

    if not new_data:
        return "No new QSO data found."

    payload = {
        "key": data["key"],
        "station_profile_id": data["station_profile_id"],
        "type": "adif",
        "string": new_data
    }

    # 启动定时任务（除测试模式外）
    global X6100_SYNC_DELAY, X6100_SYNC_TIMER
    if not data.get('nodelay'):
        X6100_SYNC_DELAY = int(data['delay'])
        if X6100_SYNC_DELAY > 0:
            X6100_SYNC_TIMER = threading.Timer(X6100_SYNC_DELAY, sync_poll_task)
            X6100_SYNC_TIMER.start()

    # 准备 HTTP 请求
    req = urllib.request.Request(
        data['endpoint'],
        data=json.dumps(payload).encode('utf-8'),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                # 上传成功后记录新的偏移量和时间戳
                timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                new_offset = filesize

                cur.execute("SELECT val FROM params WHERE name = ?", ("sync_timestamp",))
                if cur.fetchone():
                    cur.execute("UPDATE params SET val = ? WHERE name = ?", (timestamp, "sync_timestamp"))
                else:
                    cur.execute("INSERT INTO params (name, val) VALUES (?, ?)", ("sync_timestamp", timestamp))

                cur.execute("SELECT val FROM params WHERE name = ?", ("sync_log_offset",))
                if cur.fetchone():
                    cur.execute("UPDATE params SET val = ? WHERE name = ?", (new_offset, "sync_log_offset"))
                else:
                    cur.execute("INSERT INTO params (name, val) VALUES (?, ?)", ("sync_log_offset", new_offset))
                dbcon.commit()

                return f"Upload successful ({filesize - last_offset} bytes)."
            else:
                return f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL Error: {e.reason}"


# ---------- 保存配置 ----------
@app.post('/api/save_sync')
def save_sync(dbcon):
    data = bottle.request.json
    if not data:
        return {"error": "No JSON received"}

    mapping = {
        "key": "sync_key",
        "endpoint": "sync_endpoint",
        "delay": "sync_delay",
        "station_profile_id": "sync_station_profile_id"
    }

    cur = dbcon.cursor()
    for field, name in mapping.items():
        val = str(data.get(field, ''))
        row = cur.execute("SELECT val FROM params WHERE name = ?", (name,)).fetchone()
        if row:
            cur.execute("UPDATE params SET val = ? WHERE name = ?", (val, name))
        else:
            cur.execute("INSERT INTO params (name, val) VALUES (?, ?)", (name, val))
    dbcon.commit()

    # 重启定时任务
    global X6100_SYNC_DELAY, X6100_SYNC_TIMER
    if X6100_SYNC_TIMER and X6100_SYNC_TIMER.is_alive():
        X6100_SYNC_TIMER.cancel()
    X6100_SYNC_DELAY = int(data['delay'])
    if X6100_SYNC_DELAY > 0:
        X6100_SYNC_TIMER = threading.Timer(X6100_SYNC_DELAY, sync_poll_task)
        X6100_SYNC_TIMER.start()

    return {"status": "ok"}


# ---------- 获取配置 ----------
@app.get('/api/get_sync')
def get_sync(dbcon):
    mapping = {
        "key": "sync_key",
        "endpoint": "sync_endpoint",
        "delay": "sync_delay",
        "timestamp": "sync_timestamp",
        "station_profile_id": "sync_station_profile_id",
        "log_offset": "sync_log_offset"
    }

    result = {}
    cur = dbcon.cursor()
    for field, name in mapping.items():
        row = cur.execute("SELECT val FROM params WHERE name = ?", (name,)).fetchone()
        if row:
            result[field] = row[0]

    return result
