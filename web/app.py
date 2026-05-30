"""
Web 监控面板 — Flask 应用
提供 API + 简洁仪表盘 + 配置页面，查看持仓、盈亏、AI 决策历史
"""

import json
import os
import logging
from datetime import datetime
from typing import Optional

from flask import Flask, jsonify, render_template_string, request, redirect
from flask_cors import CORS

from config.settings import WEB_HOST, WEB_PORT, LOG_DIR

logger = logging.getLogger(__name__)

# Flask 应用
app = Flask(__name__)
CORS(app)

# 引擎引用（由 main.py 注入）
_engine = None
_risk_manager = None
_okx_client = None

# .env 文件路径
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", ".env")


def init_web(engine, risk_manager, okx_client):
    """初始化 Web 面板引用"""
    global _engine, _risk_manager, _okx_client
    _engine = engine
    _risk_manager = risk_manager
    _okx_client = okx_client


# ================================================================
# 配置页面 API & 页面
# ================================================================

@app.route("/config")
def config_page():
    """配置页面"""
    return render_template_string(CONFIG_HTML)


@app.route("/api/config", methods=["GET"])
def api_get_config():
    """读取当前 .env 配置（密钥脱敏）"""
    config = _read_env_file()
    # 脱敏处理
    safe = {}
    for key, value in config.items():
        if value and not value.startswith("your_"):
            safe[key] = "****"
        else:
            safe[key] = value
    safe["TRADING_MODE"] = safe.get("TRADING_MODE", "demo")
    return jsonify(safe)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存配置到 .env 文件"""
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    required = ["OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "MIMO_API_KEY"]
    missing = [k for k in required if not data.get(k) or data[k].startswith("your_")]
    if missing:
        return jsonify({"ok": False, "error": f"请填写所有必填字段: {', '.join(missing)}"}), 400

    lines = []
    for key, value in data.items():
        if value:
            lines.append(f"{key}={value}")

    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("配置已保存到 .env")
        return jsonify({"ok": True, "message": "配置已保存，重启后生效"})
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _read_env_file() -> dict:
    """读取 .env 文件内容"""
    config = {
        "OKX_API_KEY": "",
        "OKX_SECRET_KEY": "",
        "OKX_PASSPHRASE": "",
        "AI_PROVIDER": "mimo",
        "MIMO_API_KEY": "",
        "MIMO_MODEL": "mimo-v2-flash",
        "TRADING_MODE": "demo",
    }
    if not os.path.exists(ENV_FILE):
        return config
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key in config:
                        config[key] = value
    except Exception:
        pass
    return config


# ================================================================
# 配置页面 HTML（独立页面，非弹窗）
# ================================================================

CONFIG_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>配置 — OKX AI 量化交易</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #0f1117; color: #e1e4e8; min-height: 100vh; }
  .container { max-width: 640px; margin: 0 auto; padding: 40px 20px; }
  .logo { font-size: 28px; color: #58a6ff; margin-bottom: 8px; }
  .subtitle { color: #8b949e; font-size: 14px; margin-bottom: 32px; }
  .section { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 24px; margin-bottom: 20px; }
  .section h3 { font-size: 15px; color: #e1e4e8; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #21262d; }
  .section h3 .icon { margin-right: 8px; }
  .form-group { margin-bottom: 16px; }
  .form-group:last-child { margin-bottom: 0; }
  label { display: block; font-size: 13px; color: #8b949e; margin-bottom: 6px; font-weight: 500; }
  label .badge { font-size: 10px; padding: 2px 6px; border-radius: 10px; margin-left: 6px; }
  .badge.required { background: #3d1f1f; color: #f85149; }
  .badge.optional { background: #1b3824; color: #3fb950; }
  input[type="text"], input[type="password"], select {
    width: 100%; padding: 10px 14px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    color: #e1e4e8; font-size: 14px; font-family: 'Consolas', 'Courier New', monospace;
    transition: border-color 0.2s;
  }
  input:focus, select:focus { outline: none; border-color: #58a6ff; box-shadow: 0 0 0 2px rgba(88,166,255,0.15); }
  input::placeholder { color: #484f58; }
  .help { font-size: 11px; color: #484f58; margin-top: 4px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .actions { display: flex; gap: 12px; margin-top: 24px; }
  .btn { padding: 12px 28px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
  .btn-primary { background: #238636; color: white; flex: 1; }
  .btn-primary:hover { background: #2ea043; }
  .btn-primary:disabled { background: #1b3824; color: #3fb950; cursor: not-allowed; }
  .btn-secondary { background: #21262d; color: #c9d1d9; }
  .btn-secondary:hover { background: #30363d; }
  .btn-test { background: #1f2a3d; color: #58a6ff; }
  .btn-test:hover { background: #263652; }
  .toast { position: fixed; top: 20px; right: 20px; padding: 14px 24px; border-radius: 8px; font-size: 14px; z-index: 999;
    transform: translateX(110%); transition: transform 0.3s ease; max-width: 400px; }
  .toast.show { transform: translateX(0); }
  .toast.success { background: #1b3824; border: 1px solid #3fb950; color: #3fb950; }
  .toast.error { background: #3d1f1f; border: 1px solid #f85149; color: #f85149; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .status-dot.green { background: #3fb950; }
  .status-dot.red { background: #f85149; }
  .status-dot.gray { background: #484f58; }
  .back-link { display: inline-block; color: #58a6ff; font-size: 13px; margin-bottom: 20px; text-decoration: none; }
  .back-link:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back-link">← 返回仪表盘</a>
  <div class="logo">⚙️ 配置中心</div>
  <div class="subtitle">填写 API 密钥和运行参数，配置将写入 config/.env 文件</div>

  <!-- OKX 配置块 -->
  <div class="section">
    <h3><span class="icon">🔗</span>OKX 交易所 API</h3>
    <div class="form-group">
      <label>API Key <span class="badge required">必填</span></label>
      <input type="password" id="okx-key" placeholder="从 OKX 官网 API 管理页面获取" autocomplete="off">
      <div class="help">在 OKX 官网 → 账户 → API → 创建 V5 API Key（<b>只开交易权限，不要开提现！</b>）</div>
    </div>
    <div class="form-group">
      <label>Secret Key <span class="badge required">必填</span></label>
      <input type="password" id="okx-secret" placeholder="创建 API Key 时生成的 Secret" autocomplete="off">
    </div>
    <div class="form-group">
      <label>Passphrase <span class="badge required">必填</span></label>
      <input type="password" id="okx-passphrase" placeholder="创建 API Key 时设置的密码短语" autocomplete="off">
    </div>
  </div>

  <!-- MiMo API 配置块 -->
  <div class="section">
    <h3><span class="icon">🧠</span>MiMo AI API（OpenAI 兼容）</h3>
    <div class="form-group">
      <label>MiMo API Key <span class="badge required">必填</span></label>
      <input type="password" id="mimo-key" placeholder="sk-..." autocomplete="off">
      <div class="help">在 <a href="https://platform.xiaomimimo.com" target="_blank" style="color:#58a6ff;">platform.xiaomimimo.com</a> → API Keys 创建。mimo-v2-flash 每次决策约 $0.0003</div>
    </div>
    <div class="form-group">
      <label>模型</label>
      <select id="mimo-model">
        <option value="mimo-v2-flash" selected>mimo-v2-flash（快速，推荐）</option>
        <option value="mimo-v2-pro">mimo-v2-pro（推理更强）</option>
      </select>
    </div>
  </div>

  <!-- 运行模式 -->
  <div class="section">
    <h3><span class="icon">⚡</span>运行参数</h3>
    <div class="form-group">
      <label>交易模式</label>
      <select id="trading-mode">
        <option value="demo" selected>模拟盘（demo）— 安全测试，不涉及真实资金</option>
        <option value="paper">模拟交易（paper）— 真实行情 + 本地模拟账户</option>
        <option value="live">实盘（live）⚠️ — 使用真实资金交易</option>
      </select>
      <div class="help"><b>强烈建议</b>先在模拟盘跑 3-5 天，确认系统稳定后再切实盘</div>
    </div>
  </div>

  <!-- 当前状态 -->
  <div class="section" id="status-section" style="display:none;">
    <h3><span class="icon">📡</span>配置状态</h3>
    <div id="status-content"></div>
  </div>

  <!-- 按钮 -->
  <div class="actions">
    <button class="btn btn-test" onclick="testConfig()">🔍 测试连接</button>
    <button class="btn btn-primary" id="save-btn" onclick="saveConfig()">💾 保存配置</button>
  </div>
</div>

<div id="toast" class="toast"></div>

<script>
  // 加载已有配置
  async function loadConfig() {
    try {
      const resp = await fetch('/api/config');
      const data = await resp.json();
      const fill = (id, key) => {
        const el = document.getElementById(id);
        const val = data[key];
        if (val && val.includes('****')) {
          el.placeholder = '(已保存)';
        } else if (val && !val.startsWith('your_')) {
          el.value = val;
        }
      };
      fill('okx-key', 'OKX_API_KEY');
      fill('okx-secret', 'OKX_SECRET_KEY');
      fill('okx-passphrase', 'OKX_PASSPHRASE');
      fill('mimo-key', 'MIMO_API_KEY');
      document.getElementById('mimo-model').value = data.MIMO_MODEL || 'mimo-v2-flash';
      document.getElementById('trading-mode').value = data.TRADING_MODE || 'demo';

      const hasConfig = data.OKX_API_KEY && !data.OKX_API_KEY.startsWith('your_');
      updateStatus(hasConfig);
    } catch(e) {
      updateStatus(false);
    }
  }

  function updateStatus(hasConfig) {
    const section = document.getElementById('status-section');
    const content = document.getElementById('status-content');
    section.style.display = 'block';
    if (hasConfig) {
      content.innerHTML = '<span class="status-dot green"></span> OKX 密钥已配置 &nbsp;|&nbsp; <span id="ai-status-dot" class="status-dot gray"></span> MiMo AI <span id="ai-status-text">检测中...</span>';
      checkAIConfig();
    } else {
      content.innerHTML = '<span class="status-dot red"></span> 尚未配置密钥，请在下方填写';
    }
  }

  async function checkAIConfig() {
    try {
      const resp = await fetch('/api/config');
      const data = await resp.json();
      const hasAI = data.MIMO_API_KEY && !data.MIMO_API_KEY.startsWith('your_');
      document.getElementById('ai-status-dot').className = 'status-dot ' + (hasAI ? 'green' : 'red');
      document.getElementById('ai-status-text').textContent = hasAI ? '已配置' : '未配置';
    } catch(e) {}
  }

  function getFormData() {
    return {
      OKX_API_KEY: document.getElementById('okx-key').value.trim(),
      OKX_SECRET_KEY: document.getElementById('okx-secret').value.trim(),
      OKX_PASSPHRASE: document.getElementById('okx-passphrase').value.trim(),
      AI_PROVIDER: 'mimo',
      MIMO_API_KEY: document.getElementById('mimo-key').value.trim(),
      MIMO_MODEL: document.getElementById('mimo-model').value,
      TRADING_MODE: document.getElementById('trading-mode').value,
    };
  }

  function showToast(msg, type) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'toast ' + type + ' show';
    setTimeout(() => { toast.classList.remove('show'); }, 3500);
  }

  async function saveConfig() {
    const btn = document.getElementById('save-btn');
    const data = getFormData();

    // 验证
    if (!data.OKX_API_KEY || data.OKX_API_KEY.startsWith('your_')) {
      showToast('请填写 OKX API Key', 'error'); return;
    }
    if (!data.OKX_SECRET_KEY || data.OKX_SECRET_KEY.startsWith('your_')) {
      showToast('请填写 OKX Secret Key', 'error'); return;
    }
    if (!data.OKX_PASSPHRASE || data.OKX_PASSPHRASE.startsWith('your_')) {
      showToast('请填写 OKX Passphrase', 'error'); return;
    }
    if (!data.MIMO_API_KEY || data.MIMO_API_KEY.startsWith('your_')) {
      showToast('请填写 MiMo API Key', 'error'); return;
    }

    btn.disabled = true;
    btn.textContent = '保存中...';

    try {
      const resp = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await resp.json();
      if (result.ok) {
        showToast('✅ ' + result.message, 'success');
        loadConfig();
        // 清空密码框（已写入文件）
        document.getElementById('okx-key').value = '';
        document.getElementById('okx-secret').value = '';
        document.getElementById('okx-passphrase').value = '';
        document.getElementById('mimo-key').value = '';
      } else {
        showToast('❌ ' + result.error, 'error');
      }
    } catch(e) {
      showToast('❌ 保存失败: ' + e.message, 'error');
    }
    btn.disabled = false;
    btn.textContent = '💾 保存配置';
  }

  async function testConfig() {
    const data = getFormData();
    if (!data.OKX_API_KEY || data.OKX_API_KEY.startsWith('your_')) {
      showToast('请先填写 OKX API Key 再测试', 'error'); return;
    }

    // 先保存
    try {
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
    } catch(e) {
      showToast('保存失败，无法测试', 'error'); return;
    }

    showToast('🧪 配置已保存，请重启程序以测试连接。或者查看仪表盘确认连接状态。', 'success');
    loadConfig();
  }

  loadConfig();
</script>
</body>
</html>"""


# ================================================================
# API 路由
# ================================================================

@app.route("/api/status")
def api_status():
    """完整状态"""
    if not _engine or not _risk_manager:
        return jsonify({"error": "引擎未初始化"}), 503

    engine_status = _engine.get_status()
    risk_status = _risk_manager.get_status()

    return jsonify({
        "time": datetime.now().isoformat(),
        "mode": _okx_client.mode if _okx_client else "unknown",
        "engine": {
            "ai_available": engine_status["ai_available"],
            "last_ai_call": engine_status["last_ai_call"],
            "next_ai_call_in_sec": engine_status["next_ai_call_in_sec"],
            "claude_stats": engine_status["claude_stats"],
            "stop_loss_map": engine_status["stop_loss_map"],
            "take_profit_map": engine_status["take_profit_map"],
        },
        "risk": risk_status,
        "recent_trades": engine_status.get("recent_trades", []),
        "decision_history": engine_status.get("decision_history", []),
    })


@app.route("/api/positions")
def api_positions():
    """当前持仓"""
    if not _okx_client:
        return jsonify({"error": "客户端未初始化"}), 503

    state = _okx_client.get_account_state()
    positions = []
    for symbol, pos in state.positions.items():
        pnl_usdt = (pos.current_price - pos.avg_cost) * pos.amount
        positions.append({
            "symbol": symbol,
            "amount": pos.amount,
            "avg_cost": pos.avg_cost,
            "current_price": pos.current_price,
            "pnl_ratio": pos.pnl_ratio,
            "pnl_usdt": pnl_usdt,
            "usdt_value": pos.usdt_value,
        })

    return jsonify({
        "total_equity_usdt": state.total_equity_usdt,
        "available_usdt": state.available_usdt,
        "positions": positions,
    })


@app.route("/api/orders")
def api_orders():
    """未成交挂单"""
    if not _okx_client:
        return jsonify({"error": "客户端未初始化"}), 503

    orders = _okx_client.get_open_orders()
    return jsonify({"open_orders": orders})


@app.route("/api/klines")
def api_klines():
    """K线数据（给前端图表）"""
    if not _okx_client:
        return jsonify({"error": "客户端未初始化"}), 503

    symbol = request.args.get("symbol", "BTC-USDT")
    interval = request.args.get("interval", "1H")
    limit = int(request.args.get("limit", 200))

    klines = _okx_client.get_klines(symbol, interval, min(limit, 300))

    data = []
    for k in klines:
        data.append({
            "ts": k.timestamp,
            "o": k.open,
            "h": k.high,
            "l": k.low,
            "c": k.close,
            "v": k.volume,
        })

    return jsonify({"symbol": symbol, "interval": interval, "klines": data})


@app.route("/api/trades")
def api_trades():
    """成交记录（含时间戳，用于 K线 B/S 点标记）"""
    trades = []
    # Paper 模式：从历史记录取
    if _okx_client and hasattr(_okx_client, "get_trade_history"):
        trades = _okx_client.get_trade_history()
    else:
        # 实盘模式：从 AI 引擎的近期交易取
        if _engine:
            for t in _engine.get_status().get("recent_trades", []):
                trades.append({
                    "instId": t.get("symbol", ""),
                    "side": t.get("side", ""),
                    "price": t.get("price", 0),
                    "amount": t.get("amount", 0),
                    "timestamp": int(datetime.now().timestamp() * 1000),
                })
    return jsonify({"trades": trades})


@app.route("/api/manual-trade", methods=["POST"])
def api_manual_trade():
    """手动交易"""
    if not _okx_client or not _risk_manager:
        return jsonify({"ok": False, "error": "引擎未初始化"}), 503

    data = request.get_json()
    symbol = data.get("symbol", "BTC-USDT")
    side = data.get("side", "buy")
    order_type = data.get("order_type", "market")
    amount = float(data.get("amount", 0))
    price = data.get("price")

    # 构建伪 AI 决策，走风控流程
    decision = {
        "decision": side,
        "symbol": symbol,
        "amount": amount,
        "price_type": order_type,
        "limit_price": price if order_type == "limit" else None,
        "confidence": 10,
        "reasoning": "手动操作",
        "stop_loss": None,
        "take_profit": None,
    }

    state = _okx_client.get_account_state()
    current_positions = {}
    for sym, pos in state.positions.items():
        current_positions[sym] = {
            "amount": pos.amount, "avg_cost": pos.avg_cost,
            "current_price": pos.current_price, "usdt_value": pos.usdt_value,
        }

    risk_result = _risk_manager.check_decision(decision, state.total_equity_usdt, current_positions)
    if not risk_result.approved:
        return jsonify({"ok": False, "error": risk_result.reason})

    from core.order_executor import OrderExecutor
    executor = OrderExecutor(_okx_client)
    adjustments = {
        "adjusted_amount": risk_result.adjusted_amount,
        "adjusted_stop_loss": risk_result.adjusted_stop_loss,
        "adjusted_take_profit": risk_result.adjusted_take_profit,
    }
    result = executor.execute_decision(decision, adjustments)

    executed = result.get("executed", False)
    response = {"ok": executed, "details": result.get("details", "")}
    if not executed:
        response["error"] = result.get("details", "执行失败")
    return jsonify(response)


@app.route("/api/emergency", methods=["POST"])
def api_emergency():
    """紧急停止"""
    if _engine:
        _engine.emergency_stop()
        return jsonify({"result": "ok", "message": "已执行紧急停止，所有挂单已撤销"})
    return jsonify({"error": "引擎未初始化"}), 503


@app.route("/api/paper/reset", methods=["POST"])
def api_paper_reset():
    """重置模拟账户到初始状态"""
    if _okx_client and hasattr(_okx_client, "reset_state"):
        _okx_client.reset_state()
        return jsonify({"result": "ok", "message": "模拟账户已重置"})
    return jsonify({"error": "非模拟模式，无法重置"}), 400


# ================================================================
# 仪表盘页面
# ================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKX AI 量化交易 — 监控面板</title>
<!-- 使用 unpkg CDN 替代 jsdelivr（大陆可访问） -->
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #0f1117; color: #e1e4e8; padding: 16px; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px; }
  .header h1 { font-size: 20px; color: #58a6ff; }
  .header .mode { font-size: 11px; padding: 3px 8px; border-radius: 10px; }
  .mode.demo { background: #1b3824; color: #3fb950; }
  .mode.live { background: #3d1f1f; color: #f85149; }
  .mode.paper { background: #1f2a3d; color: #58a6ff; }
  .layout { display: grid; grid-template-columns: 1fr 340px; gap: 14px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .main-col { display: flex; flex-direction: column; gap: 14px; }
  .side-col { display: flex; flex-direction: column; gap: 14px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }
  .card h3 { font-size: 13px; color: #8b949e; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 1px; }
  .stat-row { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #21262d; font-size: 13px; }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: #8b949e; }
  .stat-value { font-weight: 600; font-variant-numeric: tabular-nums; }
  .positive { color: #3fb950; }
  .negative { color: #f85149; }
  .warning { color: #d29922; }
  .chart-container { width: 100%; height: 320px; }
  .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .btn-danger { background: #da3633; color: white; }
  .btn-danger:hover { background: #f85149; }
  .btn-reset { background: #1f2a3d; color: #58a6ff; }
  .btn-reset:hover { background: #263652; }
  .btn-sm { padding: 4px 8px; font-size: 11px; }
  .btn-buy { background: #238636; color: white; }
  .btn-sell { background: #da3633; color: white; }
  .decision-item { padding: 6px 0; border-bottom: 1px solid #21262d; font-size: 12px; }
  .decision-item:last-child { border-bottom: none; }
  .empty { color: #484f58; text-align: center; padding: 16px; font-size: 12px; }
  .refresh { color: #8b949e; font-size: 11px; }
  .actions-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  input, select { background: #0d1117; border: 1px solid #30363d; border-radius: 5px; color: #e1e4e8; padding: 6px 10px; font-size: 12px; width: 100%; }
  input:focus, select:focus { outline: none; border-color: #58a6ff; }
  .error-badge { background: #3d1f1f; color: #f85149; padding: 2px 6px; border-radius: 4px; font-size: 10px; }
</style>
</head>
<body>
<div class="header">
  <h1>🤖 OKX AI 量化交易</h1>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <a href="/config" style="color:#8b949e;text-decoration:none;font-size:12px;">⚙️ 配置</a>
    <span id="mode-badge" class="mode demo">-</span>
    <span id="conn-status" class="error-badge" style="display:none;">⚠️ API错误</span>
    <button class="btn btn-danger btn-sm" onclick="emergencyStop()">🛑 紧急停止</button>
    <button class="btn btn-reset btn-sm" onclick="paperReset()">🔄 重置模拟</button>
  </div>
</div>

<div class="layout">
  <!-- 左栏：K线 + 交易表单 -->
  <div class="main-col">
    <!-- K线图 -->
    <div class="card">
      <h3>📈 <span id="chart-symbol">BTC-USDT</span> — <span id="chart-price">-</span></h3>
      <div id="chart" class="chart-container"></div>
      <div style="margin-top:6px;display:flex;gap:6px;">
        <button class="btn btn-sm" onclick="switchChart('BTC-USDT')" id="btn-btc" style="background:#238636;color:white;">BTC</button>
        <button class="btn btn-sm" onclick="switchChart('ETH-USDT')" id="btn-eth" style="background:#21262d;color:#c9d1d9;">ETH</button>
        <select onchange="switchChart(this.value)" id="chart-interval" style="width:auto;">
          <option value="15m">15分钟</option>
          <option value="1H" selected>1小时</option>
          <option value="4H">4小时</option>
          <option value="1D">日K</option>
          <option value="1Y">年K</option>
        </select>
      </div>
    </div>

    <!-- 快速交易面板 -->
    <div class="card">
      <h3>📝 快速交易（手动操作）</h3>
      <div class="actions-row">
        <select id="trade-symbol" style="width:120px;">
          <option value="BTC-USDT">BTC-USDT</option>
          <option value="ETH-USDT">ETH-USDT</option>
        </select>
        <select id="trade-side" style="width:100px;">
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
        <select id="trade-type" style="width:100px;">
          <option value="market">市价</option>
          <option value="limit">限价</option>
        </select>
        <input type="number" id="trade-amount" placeholder="金额" step="10" min="15" style="width:100px;" value="50"><span style="color:#8b949e;font-size:12px;margin-left:4px;">USDT</span>
        <input type="number" id="trade-price" placeholder="限价" step="0.01" style="width:100px;display:none;">
        <button class="btn btn-buy" onclick="manualTrade()">执行</button>
        <span style="font-size:10px;color:#8b949e;">⚠️ 实盘操作，谨慎使用</span>
      </div>
      <div id="trade-result" style="font-size:11px;margin-top:6px;"></div>
    </div>
  </div>

  <!-- 右栏：数据面板 -->
  <div class="side-col">
    <div class="card">
      <h3>💰 账户</h3>
      <div id="account-stats"><span class="empty">加载中...</span></div>
    </div>

    <div class="card">
      <h3>📊 持仓</h3>
      <div id="positions"><span class="empty">加载中...</span></div>
    </div>

    <div class="card">
      <h3>🛡️ 风控</h3>
      <div id="risk-stats"><span class="empty">加载中...</span></div>
    </div>

    <div class="card">
      <h3>🧠 AI 引擎</h3>
      <div id="ai-stats"><span class="empty">加载中...</span></div>
    </div>

    <div class="card">
      <h3>💭 AI 决策</h3>
      <div id="decisions" style="max-height:200px;overflow-y:auto;"><span class="empty">暂无决策</span></div>
    </div>
  </div>
</div>

<div class="refresh" style="margin-top:10px;">
  自动刷新: 每 3 秒 | K线: 每 10 秒 | <span id="last-update"></span>
</div>

<script>
// ========== K线图 ==========
var chartSymbol = 'BTC-USDT', chartInterval = '1H';
var chart = LightweightCharts.createChart(document.getElementById('chart'), {
  layout: { background: { type:'solid', color:'#161b22' }, textColor: '#8b949e' },
  grid: { vertLines: { color:'#21262d' }, horzLines: { color:'#21262d' } },
  crosshair: { mode: 0 },
  rightPriceScale: { borderColor: '#30363d' },
  timeScale: { borderColor: '#30363d', timeVisible: true },
  height: 320,
});
var candleSeries = chart.addCandlestickSeries({
  upColor: '#3fb950', downColor: '#f85149', borderUpColor: '#3fb950', borderDownColor: '#f85149',
  wickUpColor: '#3fb950', wickDownColor: '#f85149',
});
var volumeSeries = chart.addHistogramSeries({
  priceFormat: { type: 'volume' },
  priceScaleId: '',
});
chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

async function loadChart() {
  try {
    var resp = await fetch('/api/klines?symbol=' + chartSymbol + '&interval=' + chartInterval);
    var data = await resp.json();
    if (!data.klines || data.klines.length === 0) return;
    var candleData = [], volumeData = [], klineTimes = [];
    data.klines.forEach(function(k) {
      var t = Math.floor(k.ts / 1000) + 28800;
      candleData.push({ time: t, open: k.o, high: k.h, low: k.l, close: k.c });
      volumeData.push({ time: t, value: k.v, color: k.c >= k.o ? 'rgba(63,185,80,0.3)' : 'rgba(248,81,73,0.3)' });
      klineTimes.push(t);
    });
    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);
    var last = data.klines[data.klines.length - 1];
    document.getElementById('chart-price').textContent = last.c.toLocaleString() + ' USDT';
    document.getElementById('chart-symbol').textContent = chartSymbol;

    // 加载 B/S 点
    var tResp = await fetch('/api/trades');
    var tData = await tResp.json();
    var markers = [];
    (tData.trades || []).forEach(function(t) {
      if (t.instId !== chartSymbol) return;
      var mt = Math.floor(t.timestamp / 1000) + 28800;
      // 匹配最近的 K 线时间
      var closest = null, minDiff = Infinity;
      klineTimes.forEach(function(kt) {
        var diff = Math.abs(mt - kt);
        if (diff < minDiff) { minDiff = diff; closest = kt; }
      });
      if (closest && minDiff < 86400) {
        markers.push({
          time: closest,
          position: t.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: t.side === 'buy' ? '#3fb950' : '#f85149',
          shape: t.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: t.side === 'buy' ? 'B' : 'S',
        });
      }
    });
    candleSeries.setMarkers(markers);
  } catch(e) { console.error('chart load error', e); }
}

function switchChart(sym) {
  if (sym === 'BTC-USDT' || sym === 'ETH-USDT') chartSymbol = sym;
  else { chartInterval = sym; sym = chartSymbol; }
  document.getElementById('btn-btc').style.background = chartSymbol === 'BTC-USDT' ? '#238636' : '#21262d';
  document.getElementById('btn-eth').style.background = chartSymbol === 'ETH-USDT' ? '#238636' : '#21262d';
  document.getElementById('chart-interval').value = chartInterval;
  loadChart();
}

// ========== 快速交易 ==========
document.getElementById('trade-type').addEventListener('change', function() {
  document.getElementById('trade-price').style.display = this.value === 'limit' ? '' : 'none';
});

async function manualTrade() {
  var symbol = document.getElementById('trade-symbol').value;
  var side = document.getElementById('trade-side').value;
  var type = document.getElementById('trade-type').value;
  var amount = parseFloat(document.getElementById('trade-amount').value);
  var price = parseFloat(document.getElementById('trade-price').value) || null;
  if (!amount || amount < 15) { showTradeResult('金额至少15 USDT', 'negative'); return; }
  if (type === 'limit' && !price) { showTradeResult('限价单需要填写价格', 'negative'); return; }

  try {
    var resp = await fetch('/api/manual-trade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, side, order_type: type, amount, price }),
    });
    var data = await resp.json();
    if (data.ok) {
      showTradeResult('✅ ' + side + ' ' + symbol + ' ' + amount + ' USDT (' + type + ')', 'positive');
    } else {
      showTradeResult('❌ ' + (data.error || '失败'), 'negative');
    }
    refresh();
    loadChart();
  } catch(e) { showTradeResult('网络错误', 'negative'); }
}

function showTradeResult(msg, cls) {
  var el = document.getElementById('trade-result');
  el.textContent = msg;
  el.className = cls;
  setTimeout(function() { el.textContent = ''; }, 5000);
}

// ========== 数据刷新 ==========
async function refresh() {
  try {
    var resp = await fetch('/api/status');
    var data = await resp.json();
    if (data.error) { document.getElementById('conn-status').style.display = 'inline'; return; }
    document.getElementById('conn-status').style.display = 'none';
    document.getElementById('mode-badge').className = 'mode ' + data.mode;
    document.getElementById('mode-badge').textContent = data.mode.toUpperCase();

    var posResp = await fetch('/api/positions');
    var posData = await posResp.json();
    var eq = typeof posData.total_equity_usdt === 'number' ? posData.total_equity_usdt : 0;
    var av = typeof posData.available_usdt === 'number' ? posData.available_usdt : 0;
    document.getElementById('account-stats').innerHTML =
      '<div class="stat-row"><span class="stat-label">总资产</span><span class="stat-value">' + eq.toFixed(2) + ' USDT</span></div>' +
      '<div class="stat-row"><span class="stat-label">可用</span><span class="stat-value">' + av.toFixed(2) + ' USDT</span></div>';

    var risk = data.risk || {};
    document.getElementById('risk-stats').innerHTML =
      '<div class="stat-row"><span class="stat-label">今日盈亏</span><span class="stat-value ' + (risk.daily_realized_pnl >= 0 ? 'positive' : 'negative') + '">' + (risk.daily_realized_pnl >= 0 ? '+' : '') + (risk.daily_realized_pnl || 0).toFixed(2) + ' USDT</span></div>' +
      '<div class="stat-row"><span class="stat-label">亏损上限</span><span class="stat-value">' + (risk.daily_loss_limit || 0).toFixed(2) + ' USDT</span></div>' +
      '<div class="stat-row"><span class="stat-label">熔断</span><span class="stat-value ' + (risk.meltdown_triggered ? 'negative' : 'positive') + '">' + (risk.meltdown_triggered ? '⚠️ 已触发' : '✅ 正常') + '</span></div>' +
      '<div class="stat-row"><span class="stat-label">冷却</span><span class="stat-value">' + (risk.in_cooldown ? '⏳ ' + risk.cooldown_remaining_sec + 's' : '✅ 就绪') + '</span></div>';

    var positions = posData.positions || [];
    if (positions.length === 0) {
      document.getElementById('positions').innerHTML = '<span class="empty">无持仓</span>';
    } else {
      document.getElementById('positions').innerHTML = positions.map(function(p) {
        var a = typeof p.amount === 'number' ? p.amount : 0;
        var v = typeof p.usdt_value === 'number' ? p.usdt_value : 0;
        var pnl = typeof p.pnl_ratio === 'number' ? p.pnl_ratio : 0;
        var pnlUsdt = typeof p.pnl_usdt === 'number' ? p.pnl_usdt : 0;
        return '<div class="stat-row"><span class="stat-label">' + p.symbol + '</span><span class="stat-value">' + a.toFixed(6) + ' (' + v.toFixed(2) + ' USDT)<br><span class="' + (pnl >= 0 ? 'positive' : 'negative') + '">' + (pnl >= 0 ? '+' : '') + (pnl * 100).toFixed(2) + '% (' + (pnlUsdt >= 0 ? '+' : '') + pnlUsdt.toFixed(2) + ' USDT)</span></span></div>';
      }).join('');
    }

    var eng = data.engine || {};
    document.getElementById('ai-stats').innerHTML =
      '<div class="stat-row"><span class="stat-label">AI</span><span class="stat-value ' + (eng.ai_available ? 'positive' : 'negative') + '">' + (eng.ai_available ? '✅ 在线' : '❌ 离线') + '</span></div>' +
      '<div class="stat-row"><span class="stat-label">调用次数</span><span class="stat-value">' + ((eng.claude_stats || {}).call_count || 0) + '</span></div>' +
      '<div class="stat-row"><span class="stat-label">成本</span><span class="stat-value warning">$' + ((eng.claude_stats || {}).total_cost_estimate || 0).toFixed(5) + '</span></div>' +
      '<div class="stat-row"><span class="stat-label">距下次</span><span class="stat-value">' + (eng.next_ai_call_in_sec || 0) + 's</span></div>';

    var decisions = data.decision_history || [];
    if (decisions.length === 0) {
      document.getElementById('decisions').innerHTML = '<span class="empty">暂无 AI 决策</span>';
    } else {
      document.getElementById('decisions').innerHTML = decisions.slice(-10).reverse().map(function(d) {
        return '<div class="decision-item">' + d.decision + '<br><span style="color:#8b949e;font-size:10px;">' + (d.time || '').substring(11, 19) + '</span></div>';
      }).join('');
    }

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch(e) { console.error('refresh error', e); }
}

async function emergencyStop() {
  if (!confirm('确认执行紧急停止？')) return;
  await fetch('/api/emergency', { method: 'POST' });
  refresh();
}

async function paperReset() {
  if (!confirm('重置模拟账户所有数据（余额、持仓、成交记录）？')) return;
  var resp = await fetch('/api/paper/reset', { method: 'POST' });
  var data = await resp.json();
  if (data.result === 'ok') {
    alert('✅ 模拟账户已重置');
    refresh();
    loadChart();
  } else {
    alert('❌ ' + (data.error || '重置失败'));
  }
}

refresh(); loadChart();
setInterval(refresh, 3000);
setInterval(loadChart, 10000);
</script>
</body>
</html>"""


@app.route("/")
def dashboard():
    """仪表盘主页"""
    return render_template_string(DASHBOARD_HTML)


def run_web(host: str = None, port: int = None):
    """启动 Web 服务器"""
    h = host or WEB_HOST
    p = port or WEB_PORT
    logger.info(f"Web 面板启动: http://{h}:{p}")
    app.run(host=h, port=p, debug=False, use_reloader=False)
