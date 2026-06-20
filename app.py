import os
import threading
import time
from datetime import datetime, timezone

import dash
from dash import dash_table, dcc, html
from dash.dependencies import Input, Output

from alerts import check_and_alert
from fetcher import fetch_all

_cache: dict = {"rows": [], "updated": "—"}
_lock = threading.Lock()


def refresh_loop():
    while True:
        rows = fetch_all()
        check_and_alert(rows)
        with _lock:
            _cache["rows"] = rows
            _cache["updated"] = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
        time.sleep(300)


threading.Thread(target=refresh_loop, daemon=True).start()

app = dash.Dash(__name__, title="CarryFi — Funding Rate Radar")
server = app.server

APR_THRESHOLD = float(os.getenv("APR_ALERT_THRESHOLD", "20"))

app.layout = html.Div(
    style={"backgroundColor": "#0d0d0d", "minHeight": "100vh", "fontFamily": "'Inter', sans-serif", "padding": "32px"},
    children=[
        # Header
        html.Div(
            style={"marginBottom": "28px"},
            children=[
                html.H1(
                    "⚡ CarryFi",
                    style={"color": "#f0b429", "fontSize": "2.2rem", "fontWeight": "800", "margin": "0"},
                ),
                html.P(
                    "Live funding rate farming alerts — liquid markets only, delta neutral",
                    style={"color": "#888", "margin": "6px 0 0 0", "fontSize": "0.95rem"},
                ),
            ],
        ),

        # Stats bar
        html.Div(
            id="stats-bar",
            style={"display": "flex", "gap": "20px", "marginBottom": "24px", "flexWrap": "wrap"},
        ),

        # How it works banner
        html.Div(
            style={
                "backgroundColor": "#1a1a2e",
                "border": "1px solid #2a2a4a",
                "borderRadius": "10px",
                "padding": "14px 20px",
                "marginBottom": "24px",
                "color": "#aaa",
                "fontSize": "0.87rem",
                "lineHeight": "1.7",
            },
            children=[
                html.Span("Strategy: ", style={"color": "#f0b429", "fontWeight": "700"}),
                html.Span("Buy spot", style={"color": "#4ade80", "fontWeight": "600"}),
                " + ",
                html.Span("short perp", style={"color": "#f87171", "fontWeight": "600"}),
                " on the same coin → collect funding every 1–8h with zero price exposure. "
                "All rows have >$5M daily volume and >$1M open interest. ",
                html.Span("🔥 FARM IT", style={"color": "#f0b429", "fontWeight": "700"}),
                f" rows exceed your {APR_THRESHOLD:.0f}% APR threshold — Telegram alert fires instantly.",
            ],
        ),

        # Table
        html.Div(id="table-container", style={"marginBottom": "20px"}),

        # Footer
        html.Div(id="footer", style={"color": "#444", "fontSize": "0.8rem", "marginTop": "12px"}),

        dcc.Interval(id="interval", interval=30_000, n_intervals=0),
    ],
)


def apr_color(apr: float) -> str:
    if apr >= 50:
        return "#f0b429"
    if apr >= 20:
        return "#4ade80"
    if apr > 0:
        return "#888"
    return "#f87171"


@app.callback(
    Output("stats-bar", "children"),
    Output("table-container", "children"),
    Output("footer", "children"),
    Input("interval", "n_intervals"),
)
def update(_):
    with _lock:
        rows = _cache["rows"]
        updated = _cache["updated"]

    if not rows:
        return [], html.P("Loading data...", style={"color": "#888"}), ""

    hot = [r for r in rows if r["apr"] >= APR_THRESHOLD]
    top_apr = max((r["apr"] for r in rows), default=0)

    def stat_card(label, value, color="#f0b429"):
        return html.Div(
            style={
                "backgroundColor": "#1a1a1a",
                "border": "1px solid #2a2a2a",
                "borderRadius": "10px",
                "padding": "14px 20px",
                "minWidth": "140px",
            },
            children=[
                html.P(label, style={"color": "#666", "fontSize": "0.75rem", "margin": "0 0 4px 0"}),
                html.P(value, style={"color": color, "fontSize": "1.3rem", "fontWeight": "700", "margin": "0"}),
            ],
        )

    stats = [
        stat_card("Liquid Markets", str(len(rows))),
        stat_card(f"🔥 Above {APR_THRESHOLD:.0f}% APR", str(len(hot)), "#f0b429" if hot else "#555"),
        stat_card("Best APR", f"{top_apr:.1f}%", apr_color(top_apr)),
        stat_card("Updated", updated, "#555"),
    ]

    table_data = []
    style_data_conditional = []

    for i, r in enumerate(rows[:50]):
        apr = r["apr"]
        signal = "🔥 FARM IT" if apr >= APR_THRESHOLD else ("✅ Decent" if apr >= 10 else "⚪ Low")
        table_data.append({
            "Coin": r["coin"],
            "Exchange": r["exchange"],
            "Rate": r["rate_display"],
            "APR ▼": f"{apr:.2f}%",
            "24h Volume": r["volume_24h"],
            "Open Interest": r["open_interest"],
            "Next Funding": r["next_funding"],
            "Signal": signal,
        })
        if apr >= APR_THRESHOLD:
            style_data_conditional.append({
                "if": {"row_index": i},
                "backgroundColor": "#1a1500",
                "borderTop": "1px solid #f0b429",
                "borderBottom": "1px solid #f0b429",
            })

    cols = ["Coin", "Exchange", "Rate", "APR ▼", "24h Volume", "Open Interest", "Next Funding", "Signal"]
    table = dash_table.DataTable(
        data=table_data,
        columns=[{"name": c, "id": c} for c in cols],
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#161616",
            "color": "#f0b429",
            "fontWeight": "700",
            "border": "1px solid #222",
            "fontSize": "0.78rem",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
            "padding": "10px 16px",
        },
        style_data={
            "backgroundColor": "#111",
            "color": "#ccc",
            "border": "1px solid #1a1a1a",
            "fontSize": "0.9rem",
        },
        style_cell={"padding": "10px 16px", "fontFamily": "'Inter', sans-serif"},
        style_data_conditional=style_data_conditional,
        sort_action="native",
        page_size=50,
    )

    footer = f"Filtered to >$5M 24h volume · {len(rows)} liquid markets · Hyperliquid, OKX, Gate.io · refreshes every 5 min"
    return stats, table, footer


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
